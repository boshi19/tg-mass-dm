"""压缩长时间运行诊断脚本。

启动本地 WebUI，触发 dry-run 发送任务，并采集状态接口和进程指标。
不会实际发送 Telegram 消息。
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "reports"


def _json_request(method: str, url: str, body: dict | None = None, timeout: float = 5) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _wait_until_ready(base_url: str, timeout: float) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            _json_request("GET", f"{base_url}/api/status", timeout=2)
            return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"WebUI 未在 {timeout}s 内就绪: {last_error}")


def _metrics_for(proc: subprocess.Popen) -> dict:
    try:
        import psutil
    except ImportError:
        return {
            "rss_mb": "",
            "cpu_percent": "",
            "threads": "",
            "handles": "",
        }

    p = psutil.Process(proc.pid)
    mem = p.memory_info().rss / 1024 / 1024
    handles = p.num_handles() if hasattr(p, "num_handles") else ""
    return {
        "rss_mb": round(mem, 2),
        "cpu_percent": p.cpu_percent(interval=None),
        "threads": p.num_threads(),
        "handles": handles,
    }


def _write_diagnosis(samples: list[dict], errors: list[str], duration: int) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "DIAGNOSIS.md"
    start_mem = next((s.get("rss_mb") for s in samples if s.get("rss_mb") not in ("", None)), None)
    end_mem = next((s.get("rss_mb") for s in reversed(samples) if s.get("rss_mb") not in ("", None)), None)
    leak_text = "未采集到 psutil 内存指标"
    if isinstance(start_mem, (int, float)) and isinstance(end_mem, (int, float)) and start_mem > 0:
        ratio = (end_mem - start_mem) / start_mem
        leak_text = f"起始 {start_mem} MB，结束 {end_mem} MB，增幅 {ratio * 100:.1f}%"
        if ratio > 0.2:
            leak_text += "，疑似内存泄漏"

    last = samples[-1] if samples else {}
    status_ok = "是" if samples and not errors else "否"
    content = [
        "# 长时间运行诊断报告",
        "",
        f"- 采样时长: {duration} 秒",
        f"- /api/status 持续响应: {status_ok}",
        f"- 内存趋势: {leak_text}",
        f"- 最后任务状态: {last.get('state', 'unknown')}",
        f"- 最后心跳: {last.get('last_heartbeat', '')}",
        f"- 最后动作: {last.get('last_action', '')}",
        f"- 最后错误: {last.get('last_error', '') or '无'}",
        "",
        "## 错误",
    ]
    if errors:
        content.extend(f"- {e}" for e in errors[-10:])
    else:
        content.append("- 未发现状态接口错误")
    content.extend([
        "",
        "## 输出文件",
        "- runtime_metrics.csv: 进程和状态采样",
        "- thread_stack.txt: watchdog 发现心跳停滞时自动生成",
    ])
    path.write_text("\n".join(content) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="tg-mass-dm 长运行诊断")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--duration", type=int, default=120)
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    base_url = f"http://{args.host}:{args.port}"
    metrics_path = REPORTS_DIR / "runtime_metrics.csv"
    server_log = REPORTS_DIR / "longrun_server.log"
    errors: list[str] = []
    samples: list[dict] = []

    with server_log.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app:app",
                "--host",
                args.host,
                "--port",
                str(args.port),
                "--log-level",
                "info",
            ],
            cwd=str(BASE_DIR),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_until_ready(base_url, timeout=30)
            _json_request("POST", f"{base_url}/api/send/start", {"dry_run": True, "now": True}, timeout=10)

            with metrics_path.open("w", newline="", encoding="utf-8") as f:
                fields = [
                    "elapsed",
                    "rss_mb",
                    "cpu_percent",
                    "threads",
                    "handles",
                    "state",
                    "sent",
                    "failed",
                    "last_heartbeat",
                    "last_action",
                    "last_error",
                    "current_target",
                    "waiting",
                ]
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                started = time.time()
                while time.time() - started < args.duration:
                    elapsed = round(time.time() - started, 1)
                    row = {"elapsed": elapsed, **_metrics_for(proc)}
                    try:
                        status = _json_request("GET", f"{base_url}/api/status", timeout=5)
                        task = status.get("task", {})
                        row.update({
                            "state": task.get("state"),
                            "sent": task.get("sent"),
                            "failed": task.get("failed"),
                            "last_heartbeat": task.get("last_heartbeat"),
                            "last_action": task.get("last_action"),
                            "last_error": task.get("last_error"),
                            "current_target": task.get("current_target"),
                            "waiting": task.get("waiting"),
                        })
                    except Exception as exc:
                        errors.append(f"{elapsed}s status error: {exc}")
                    writer.writerow(row)
                    samples.append(row)
                    time.sleep(1)
        finally:
            try:
                _json_request("POST", f"{base_url}/api/send/stop", timeout=3)
            except (urllib.error.URLError, TimeoutError, Exception):
                pass
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    report = _write_diagnosis(samples, errors, args.duration)
    print(f"诊断完成: {report}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
