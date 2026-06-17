# main.py - WebUI 启动入口 (v4.1-web)
# 启动 FastAPI + uvicorn 服务，浏览器访问 http://127.0.0.1:8000

import argparse
import sys
import os
import threading
import webbrowser
from pathlib import Path


def main():
    # 自动创建 sessions/ 目录（防止 Telethon 会话文件写入失败）
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).resolve().parent
        sessions_dir = exe_dir / "sessions"
    else:
        base_dir = Path(__file__).resolve().parent
        sessions_dir = base_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    parser = argparse.ArgumentParser(description="tg-mass-dm WebUI 启动器")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()

    print("=" * 55)
    print("  tg-mass-dm WebUI v4.1-web")
    print("=" * 55)
    print(f"  访问地址: http://{args.host}:{args.port}")
    print(f"  按 Ctrl+C 停止服务\n")
    print("  提示: 浏览器已自动打开，无需手动输入地址")
    print("  服务器运行中，后台日志将在此显示，请勿关闭此窗口\n")

    try:
        # 启动后 1.5 秒自动打开浏览器
        def _auto_open_browser():
            import time
            time.sleep(1.5)
            try:
                webbrowser.open(f"http://{args.host}:{args.port}")
            except Exception:
                pass  # 浏览器打开失败不阻塞主流程
        
        threading.Thread(target=_auto_open_browser, daemon=True).start()
        
        import uvicorn
    except ImportError:
        msg = "[错误] 未安装 uvicorn，请先执行：pip install -r requirements.txt\n"
        print(msg)
        _pause_and_exit(msg)

    try:
        uvicorn.run(
            "app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
        )
    except SystemExit as e:
        # uvicorn 在端口冲突时内部调用 sys.exit(1)，抛出 SystemExit
        # 注意：SystemExit 继承自 BaseException，不被 except Exception 捕获
        if e.code == 1:
            msg = _port_conflict_msg()
        else:
            msg = f"\n[错误] uvicorn 异常退出，代码: {e.code}\n"
        print(msg)
        _write_error_log(msg)
    except OSError as e:
        if "10048" in str(e) or "address already in use" in str(e).lower():
            msg = _port_conflict_msg()
        else:
            msg = f"\n[错误] 启动失败！\n{e}\n"
        print(msg)
        _write_error_log(msg)
    except Exception as e:
        msg = (
            "\n" + "=" * 50 +
            "\n  !! 程序启动异常 !!" +
            "\n" + "=" * 50 +
            f"\n  错误信息: {e}" +
            "\n" + "=" * 50 +
            "\n  请检查：" +
            "\n  . config.yaml 是否存在且格式正确" +
            "\n  . 是否缺少其他依赖文件" +
            "\n" + "=" * 50 +
            "\n"
        )
        print(msg)
        _write_error_log(msg)


def _port_conflict_msg() -> str:
    return (
        "\n" + "=" * 50 +
        "\n  !! 启动失败：端口 8000 已被占用 !!" +
        "\n" + "=" * 50 +
        "\n  可能的原因：" +
        "\n  . 程序已在前一个后台运行" +
        "\n  . 其他软件占用了 8000 端口" +
        "\n" +
        "\n  解决方法：" +
        "\n  1. 关闭已运行的程序" +
        "\n  2. 或重启电脑后再试" +
        "\n" + "=" * 50 +
        "\n"
    )


def _write_error_log(msg: str) -> None:
    """将错误信息写入 error.log，方便事后排查。"""
    try:
        with open("error.log", "w", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass  # 写日志失败不应影响主流程


def _pause_and_exit(msg: str = "") -> None:
    """保留控制台窗口，让用户看到错误信息后手动关闭。"""
    if msg:
        _write_error_log(msg)
    try:
        input("\n按 Enter 键退出...")
    except (EOFError, KeyboardInterrupt):
        # 非交互环境或 Ctrl+C，安静退出即可
        pass
    sys.exit(1)


if __name__ == "__main__":
    main()
