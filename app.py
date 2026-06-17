# app.py - FastAPI 应用：所有 API 路由 + 静态服务 + SSE 日志流

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import AppConfig, load_config, save_config
from sender import send_messages
from task_manager import manager
from event_bus import bus
from telethon import TelegramClient, connection
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

if getattr(sys, 'frozen', False):
    # 打包运行：配置文件在 exe 同级目录，静态网页在 PyInstaller 临时解压目录
    EXE_DIR = Path(sys.executable).resolve().parent
    BASE_DIR = EXE_DIR
    CONFIG_PATH = EXE_DIR / "config.yaml"
    STATIC_DIR = Path(sys._MEIPASS) / "static"
else:
    # 源码运行
    BASE_DIR = Path(__file__).resolve().parent
    CONFIG_PATH = BASE_DIR / "config.yaml"
    STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="tg-mass-dm WebUI", version="4.1-web")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ═══════════════════════════════════════════
#  请求体模型
# ═══════════════════════════════════════════


class ConfigUpdate(BaseModel):
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    session_file: Optional[str] = None
    usernames_file: Optional[str] = None
    messages_file: Optional[str] = None
    proxy_type: Optional[str] = None       # socks5 / socks4 / http / ""
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    start_time: Optional[str] = None
    timezone: Optional[str] = None
    wait_if_past: Optional[bool] = None
    delay_min: Optional[float] = None
    delay_max: Optional[float] = None
    daily_limit: Optional[int] = None
    tail_enabled: Optional[bool] = None
    tail_style: Optional[str] = None
    dry_run: Optional[bool] = None


class TextListUpdate(BaseModel):
    text: str


class SendRequest(BaseModel):
    dry_run: bool = False
    now: bool = True      # WebUI 默认跳过定时立即执行


class SessionLoginRequest(BaseModel):
    phone: str             # 手机号（国际格式，如 +8613800138000）


class SessionCodeRequest(BaseModel):
    login_id: str
    code: str


class SessionPasswordRequest(BaseModel):
    login_id: str
    password: str


PENDING_LOGINS: dict[str, dict] = {}


# ═══════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════


def _read_text_lines(path: Path) -> list[str]:
    """读取文本文件，返回非空非注释行列表。"""
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _read_raw(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _write_raw(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _list_sessions(cfg: AppConfig) -> list[dict]:
    """
    列出 sessions 目录下的 .session 文件。

    sessions 目录固定为程序目录下的 sessions/，避免在 exe 同级目录之外创建凭证。
    rel_path 可直接写回 config.yaml 的 session_file 字段。
    """
    import os
    sessions_dir = BASE_DIR / "sessions"
    current_name = Path(cfg.session_file).name
    result = []
    if sessions_dir.exists():
        for f in sorted(sessions_dir.glob("*.session")):
            # 不含 .session 后缀的相对路径（与 config.yaml 写法一致）
            rel = os.path.relpath(str(f.with_suffix("")), str(BASE_DIR)).replace("\\", "/")
            result.append({
                "name": f.stem,
                "filename": f.name,
                "rel_path": rel,
                "current": f.stem == current_name,
            })
    return result


def _get_sessions_dir(cfg: AppConfig) -> Path:
    """获取程序目录下的 sessions 目录路径，不存在则创建。"""
    sessions_dir = BASE_DIR / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def _session_rel(stem: str) -> str:
    """session 文件名（不含 .session）转相对 BASE_DIR 路径。"""
    import os
    # 查找实际文件位置
    for candidate in [BASE_DIR / "sessions" / f"{stem}.session", BASE_DIR / f"{stem}.session"]:
        if candidate.exists():
            return os.path.relpath(str(candidate.with_suffix("")), str(BASE_DIR)).replace("\\", "/")
    return f"sessions/{stem}"


def _session_rel_from_path(session_base: Path) -> str:
    """把不含 .session 后缀的绝对 session 路径转成配置相对路径。"""
    import os
    return os.path.relpath(str(session_base), str(BASE_DIR)).replace("\\", "/")


async def _build_login_client(cfg: AppConfig, session_base: Path) -> TelegramClient:
    """创建登录专用 TelegramClient，沿用配置里的代理。"""
    proxy_params = None
    conn_type = None
    if cfg.proxy:
        import socks
        ptype = (cfg.proxy.get("type") or "socks5").lower()
        type_map = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}
        proxy_params = (
            type_map.get(ptype, socks.SOCKS5),
            cfg.proxy.get("host", "127.0.0.1"),
            int(cfg.proxy.get("port", 1080)),
            True,
            cfg.proxy.get("username") or None,
            cfg.proxy.get("password") or None,
        )
        if ptype == "http":
            conn_type = connection.ConnectionTcpMTProxyRandomizedIntermediate

    kwargs = {"proxy": proxy_params}
    if conn_type is not None:
        kwargs["connection"] = conn_type
    client = TelegramClient(str(session_base), cfg.api_id, cfg.api_hash, **kwargs)
    await client.connect()
    return client


async def _close_pending_login(login_id: str) -> None:
    pending = PENDING_LOGINS.pop(login_id, None)
    if not pending:
        return
    client = pending.get("client")
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass


def _activate_session(session_base: Path) -> str:
    cfg = load_config(str(CONFIG_PATH))
    rel = _session_rel_from_path(session_base)
    cfg.session_file = rel
    cfg.config_dir = BASE_DIR
    cfg.__post_init__()
    save_config(str(CONFIG_PATH), cfg)
    return rel


# ═══════════════════════════════════════════
#  页面路由
# ═══════════════════════════════════════════


@app.get("/", response_class=HTMLResponse)
async def index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>static/index.html 不存在</h1>", status_code=500)
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════
#  配置 API
# ═══════════════════════════════════════════


@app.get("/api/config")
async def get_config():
    try:
        cfg = load_config(str(CONFIG_PATH))
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    d = cfg.to_dict()
    return {
        "api_id": d["api_id"],
        "api_hash": d["api_hash"],
        "session_file": d["session_file"],
        "usernames_file": d["usernames_file"],
        "messages_file": d["messages_file"],
        "start_time": d["schedule"]["start_time"],
        "timezone": d["schedule"]["timezone"],
        "wait_if_past": d["schedule"]["wait_if_past"],
        "delay_min": d["delay"]["min"],
        "delay_max": d["delay"]["max"],
        "daily_limit": d["daily_limit"],
        "tail_enabled": d["random_tail"]["enabled"],
        "tail_style": d["random_tail"]["style"],
        "dry_run": d["dry_run"],
    }


@app.put("/api/config")
async def update_config(body: ConfigUpdate):
    try:
        cfg = load_config(str(CONFIG_PATH))
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    data = body.model_dump(exclude_none=True)
    if "api_id" in data:
        cfg.api_id = data["api_id"]
    if "api_hash" in data:
        cfg.api_hash = data["api_hash"]
    if "session_file" in data:
        cfg.session_file = data["session_file"]
        cfg.config_dir = BASE_DIR
        cfg.__post_init__()
    if "usernames_file" in data:
        cfg.usernames_file = data["usernames_file"]
    if "messages_file" in data:
        cfg.messages_file = data["messages_file"]
    if "start_time" in data:
        cfg.schedule.start_time = data["start_time"] or None
    if "timezone" in data:
        cfg.schedule.timezone = data["timezone"]
    if "wait_if_past" in data:
        cfg.schedule.wait_if_past = data["wait_if_past"]
    if "delay_min" in data:
        cfg.delay.min = float(data["delay_min"])
    if "delay_max" in data:
        cfg.delay.max = float(data["delay_max"])
    if "daily_limit" in data:
        cfg.daily_limit = int(data["daily_limit"])
    if "tail_enabled" in data:
        cfg.random_tail.enabled = bool(data["tail_enabled"])
    if "tail_style" in data:
        cfg.random_tail.style = data["tail_style"]
    if "dry_run" in data:
        cfg.dry_run = bool(data["dry_run"])

    save_config(str(CONFIG_PATH), cfg)
    return {"ok": True, "message": "配置已保存"}


# ═══════════════════════════════════════════
#  目标用户 API
# ═══════════════════════════════════════════


@app.get("/api/targets")
async def get_targets():
    try:
        cfg = load_config(str(CONFIG_PATH))
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    path = cfg.config_dir / cfg.usernames_file
    lines = _read_text_lines(path)
    return {"count": len(lines), "items": lines, "raw": _read_raw(path)}


@app.put("/api/targets")
async def update_targets(body: TextListUpdate):
    try:
        cfg = load_config(str(CONFIG_PATH))
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    path = cfg.config_dir / cfg.usernames_file
    # 去重保持顺序
    seen = set()
    cleaned = []
    for line in body.text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s not in seen:
            seen.add(s)
            cleaned.append(s)
    _write_raw(path, "\n".join(cleaned) + ("\n" if cleaned else ""))
    return {"ok": True, "count": len(cleaned), "items": cleaned}


# ═══════════════════════════════════════════
#  文案池 API
# ═══════════════════════════════════════════


@app.get("/api/messages")
async def get_messages():
    try:
        cfg = load_config(str(CONFIG_PATH))
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    path = cfg.config_dir / cfg.messages_file
    lines = _read_text_lines(path)
    return {"count": len(lines), "items": lines, "raw": _read_raw(path)}


@app.put("/api/messages")
async def update_messages(body: TextListUpdate):
    try:
        cfg = load_config(str(CONFIG_PATH))
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    path = cfg.config_dir / cfg.messages_file
    cleaned = [
        line.strip()
        for line in body.text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    _write_raw(path, "\n".join(cleaned) + ("\n" if cleaned else ""))
    return {"ok": True, "count": len(cleaned), "items": cleaned}


# ═══════════════════════════════════════════
#  Session API
# ═══════════════════════════════════════════


@app.get("/api/sessions")
async def get_sessions():
    try:
        cfg = load_config(str(CONFIG_PATH))
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"current": cfg.session_file, "items": _list_sessions(cfg)}


@app.put("/api/sessions/active")
async def set_active_session(body: dict):
    """切换当前使用的 session 文件。"""
    rel = body.get("rel_path") or body.get("name")
    if not rel:
        raise HTTPException(status_code=400, detail="缺少 rel_path 或 name")
    try:
        cfg = load_config(str(CONFIG_PATH))
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 统一存为相对 BASE_DIR 的路径写法
    cfg.session_file = rel
    cfg.config_dir = BASE_DIR
    cfg.__post_init__()
    save_config(str(CONFIG_PATH), cfg)
    return {"ok": True, "session_file": cfg.session_file}


@app.post("/api/sessions/login")
async def session_login(body: SessionLoginRequest):
    """
    发起 Telegram 登录：连接客户端并向手机号发送验证码。
    """
    try:
        cfg = load_config(str(CONFIG_PATH))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"加载配置失败: {str(e)}")

    sessions_dir = _get_sessions_dir(cfg)
    phone = body.phone.strip()
    safe_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
    if not phone.startswith("+") or not safe_phone.isdigit():
        raise HTTPException(status_code=400, detail="请输入国际格式手机号，例如 +8613800000000")

    session_base = sessions_dir / safe_phone
    client = None
    try:
        client = await _build_login_client(cfg, session_base)
        if await client.is_user_authorized():
            await client.disconnect()
            rel = _activate_session(session_base)
            await bus.publish("success", f"账号 {phone} 已经授权，已切换为活动 Session", "status")
            return {"ok": True, "step": "complete", "session_file": rel, "message": "该账号已经登录，已切换为活动 Session。"}

        sent = await client.send_code_request(phone)
        login_id = uuid.uuid4().hex
        PENDING_LOGINS[login_id] = {
            "client": client,
            "phone": phone,
            "session_base": session_base,
            "phone_code_hash": sent.phone_code_hash,
        }
        await bus.publish("info", f"验证码已发送到 {phone}，请在网页弹窗输入验证码", "status")
        return {"ok": True, "step": "code", "login_id": login_id, "message": "验证码已发送，请输入 Telegram 收到的验证码。"}
    except PhoneNumberInvalidError:
        if client:
            await client.disconnect()
        raise HTTPException(status_code=400, detail="手机号格式无效，请使用国际格式，例如 +8613800000000")
    except FloodWaitError as e:
        if client:
            await client.disconnect()
        raise HTTPException(status_code=429, detail=f"请求验证码过于频繁，请等待 {e.seconds} 秒后再试")
    except Exception as e:
        if client:
            await client.disconnect()
        raise HTTPException(status_code=500, detail=f"发起登录失败: {str(e)}")


@app.post("/api/sessions/login/code")
async def session_login_code(body: SessionCodeRequest):
    """提交 Telegram 验证码，完成登录或进入二步密码环节。"""
    pending = PENDING_LOGINS.get(body.login_id)
    if not pending:
        raise HTTPException(status_code=400, detail="登录会话已失效，请重新发送验证码")

    client: TelegramClient = pending["client"]
    code = body.code.strip().replace(" ", "")
    try:
        await client.sign_in(
            phone=pending["phone"],
            code=code,
            phone_code_hash=pending["phone_code_hash"],
        )
        await _close_pending_login(body.login_id)
        rel = _activate_session(pending["session_base"])
        await bus.publish("success", f"账号 {pending['phone']} 登录成功，已切换为活动 Session", "status")
        return {"ok": True, "step": "complete", "session_file": rel, "message": "登录成功，已切换为活动 Session。"}
    except SessionPasswordNeededError:
        await bus.publish("info", f"账号 {pending['phone']} 需要二步验证密码", "status")
        return {"ok": True, "step": "password", "message": "该账号开启了二步验证，请输入 Telegram 云密码。"}
    except PhoneCodeInvalidError:
        raise HTTPException(status_code=400, detail="验证码不正确，请重新输入")
    except PhoneCodeExpiredError:
        await _close_pending_login(body.login_id)
        raise HTTPException(status_code=400, detail="验证码已过期，请重新发送验证码")
    except Exception as e:
        await _close_pending_login(body.login_id)
        raise HTTPException(status_code=500, detail=f"验证码登录失败: {str(e)}")


@app.post("/api/sessions/login/password")
async def session_login_password(body: SessionPasswordRequest):
    """提交二步验证密码完成登录。"""
    pending = PENDING_LOGINS.get(body.login_id)
    if not pending:
        raise HTTPException(status_code=400, detail="登录会话已失效，请重新发送验证码")

    client: TelegramClient = pending["client"]
    try:
        await client.sign_in(password=body.password)
        await _close_pending_login(body.login_id)
        rel = _activate_session(pending["session_base"])
        await bus.publish("success", f"账号 {pending['phone']} 二步验证通过，已切换为活动 Session", "status")
        return {"ok": True, "step": "complete", "session_file": rel, "message": "登录成功，已切换为活动 Session。"}
    except PasswordHashInvalidError:
        raise HTTPException(status_code=400, detail="二步验证密码不正确，请重新输入")
    except Exception as e:
        await _close_pending_login(body.login_id)
        raise HTTPException(status_code=500, detail=f"二步验证失败: {str(e)}")


@app.delete("/api/sessions")
async def delete_session(body: dict):
    """
    根据前端传过来的相对路径，从磁盘安全删除特定的 .session 文件
    """
    rel_path = body.get("rel_path")
    if not rel_path:
        raise HTTPException(status_code=400, detail="参数缺失: rel_path")
        
    try:
        cfg = load_config(str(CONFIG_PATH))
        # 还原出文件的绝对物理路径
        target_file = (BASE_DIR / f"{rel_path}.session").resolve()
        
        if not target_file.exists():
            raise HTTPException(status_code=404, detail="未在磁盘找到该 Session 文件")
            
        # 绝不允许删除当前正在激活激活使用的 session
        if target_file.stem == Path(cfg.session_file).name:
            raise HTTPException(status_code=400, detail="无法删除当前正在激活运行的账号，请先切换到其他账号")
            
        # 执行物理删除
        target_file.unlink()
        await bus.publish("info", f"成功从磁盘清除了 Session 文件: {target_file.name}", "status")
        return {"ok": True, "message": f"成功删除凭证: {target_file.name}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除文件失败: {str(e)}")


# ═══════════════════════════════════════════
#  状态 + 发送控制 API
# ═══════════════════════════════════════════


@app.get("/api/status")
async def get_status():
    try:
        cfg = load_config(str(CONFIG_PATH))
    except (FileNotFoundError, ValueError):
        cfg = None
    return {
        "task": manager.stats.to_dict(),
        "config_ok": cfg is not None,
        "version": "4.1-web",
    }


async def _build_send_coro(dry_run: bool, now: bool):
    """构造发送协程：加载最新配置，调用 send_messages。"""
    cfg = load_config(str(CONFIG_PATH))
    if not now:
        # 定时模式：进入异步等待
        from scheduler import async_wait_until_scheduled
        ok = await async_wait_until_scheduled(cfg.schedule, manager.hooks())
        if not ok:
            await bus.publish("warn", "定时等待期间被停止", "status")
            return
    await send_messages(cfg, dry_run=dry_run, hooks=manager.hooks(), stats_ref=manager.stats)


@app.post("/api/send/start")
async def send_start(body: SendRequest):
    if manager.is_running():
        raise HTTPException(status_code=409, detail="已有任务运行中")
    try:
        coro = _build_send_coro(dry_run=body.dry_run, now=body.now)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    ok = await manager.start(coro)
    if not ok:
        raise HTTPException(status_code=409, detail="启动失败")
    return {"ok": True, "dry_run": body.dry_run, "now": body.now}


@app.post("/api/send/pause")
async def send_pause():
    ok = await manager.pause()
    return {"ok": ok}


@app.post("/api/send/resume")
async def send_resume():
    ok = await manager.resume()
    return {"ok": ok}


@app.post("/api/send/stop")
async def send_stop():
    ok = await manager.stop()
    return {"ok": ok}


# ═══════════════════════════════════════════
#  SSE 日志流
# ═══════════════════════════════════════════


@app.get("/api/logs/stream")
async def logs_stream(request: Request):
    async def event_generator():
        q = await bus.subscribe()
        try:
            # 先发一个心跳，让前端确认连接
            yield 'data: {"ts":0,"level":"info","message":"已连接日志流","category":"status"}\n\n'
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                    yield event.to_sse()
                except asyncio.TimeoutError:
                    # 心跳保活
                    yield ": heartbeat\n\n"
        finally:
            await bus.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
