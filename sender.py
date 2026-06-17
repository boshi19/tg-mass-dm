# sender.py - 核心发送逻辑 (Telethon 交互)
# WebUI 版：通过 hooks 回调上报事件，支持暂停/停止，与 task_manager 协作。

import asyncio
import inspect
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

import logging
logging.getLogger("telethon").setLevel(logging.WARNING)

from telethon import TelegramClient, connection
from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    RPCError,
    UserNotMutualContactError,
    UserPrivacyRestrictedError,
)

from config import AppConfig
from history import count_success_today, history_path, record_send, successful_targets_today
from messages import compose_message, detect_risky_terms, load_messages
from targets import load_targets, remove_target
from scheduler import async_sleep


@dataclass
class SendResult:
    """发送结果汇总"""
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    removed: int = 0
    removed_usernames: list[str] = field(default_factory=list)


# hooks 约定：均为 async，由调用方（task_manager）实现
Hooks = dict  # {"on_log", "on_progress", "on_activity", "should_pause", "should_stop"}
CONNECT_TIMEOUT = 45
AUTH_TIMEOUT = 20
SEND_TIMEOUT = 45
FLOOD_WAIT_STEP = 5
PERMANENT_FAILURE = "permanent"
TEMPORARY_FAILURE = "temporary"
NETWORK_FAILURE = "network"


async def safe_disconnect(client: TelegramClient) -> None:
    """安全断开 Telethon 连接，规避 set_update_state 属性的兼容错误"""
    try:
        await asyncio.wait_for(client.disconnect(), timeout=10)
    except (TypeError, asyncio.TimeoutError):
        try:
            await asyncio.wait_for(client._disconnect(), timeout=10)
        except Exception:
            pass
        try:
            result = client.session.close()
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, timeout=5)
        except Exception:
            pass


async def _log(hooks: Hooks | None, level: str, message: str, category: str = "log") -> None:
    """通过 hooks 上报一条日志；无 hooks 时回退到 print（CLI 兼容）。"""
    if hooks and hooks.get("on_log"):
        await hooks["on_log"](level, message, category)
    else:
        print(message)


async def _stopped(hooks: Hooks | None) -> bool:
    return bool(hooks and hooks.get("should_stop") and await hooks["should_stop"]())


async def _paused(hooks: Hooks | None) -> bool:
    return bool(hooks and hooks.get("should_pause") and await hooks["should_pause"]())


async def _activity(
    hooks: Hooks | None,
    action: str | None = None,
    current_target: str | None = None,
    waiting: bool | None = None,
) -> None:
    if hooks and hooks.get("on_activity"):
        await hooks["on_activity"](action, current_target, waiting)


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, (UserPrivacyRestrictedError, UserNotMutualContactError)):
        return PERMANENT_FAILURE
    if isinstance(exc, FloodWaitError):
        return TEMPORARY_FAILURE
    if isinstance(exc, (asyncio.TimeoutError, OSError, ConnectionError)):
        return NETWORK_FAILURE
    if isinstance(exc, RPCError):
        text = str(exc).upper()
        permanent_markers = [
            "USER_PRIVACY_RESTRICTED",
            "USER_NOT_MUTUAL_CONTACT",
            "USERNAME_INVALID",
            "USERNAME_NOT_OCCUPIED",
            "PEER_ID_INVALID",
        ]
        if any(marker in text for marker in permanent_markers):
            return PERMANENT_FAILURE
        return TEMPORARY_FAILURE
    return TEMPORARY_FAILURE


def _message_preview(text: str) -> str:
    return text.replace("\n", " ")[:80]


def _record_history_safe(
    db_path: Path,
    account: str,
    target: str,
    status: str,
    failure_category: str = "",
    message: str = "",
    error: str = "",
) -> None:
    try:
        record_send(
            db_path,
            account=account,
            target=target,
            status=status,
            failure_category=failure_category,
            message_preview=_message_preview(message),
            error=error,
        )
    except Exception:
        pass


def _write_dry_run_report(
    cfg: AppConfig,
    account: str,
    targets: list[str],
    samples: list[tuple[str, str, list[str]]],
    remaining_quota: int,
    skipped_resume: int,
) -> Path:
    reports_dir = cfg.config_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "dry_run_preview.md"
    risky = [(target, terms) for target, _text, terms in samples if terms]
    lines = [
        "# Dry Run 预览报告",
        "",
        f"- 账号: {account}",
        f"- 目标数量: {len(targets)}",
        f"- 今日剩余额度: {remaining_quota}",
        f"- 已因历史成功记录跳过: {skipped_resume}",
        f"- 预计发送/预览: {min(len(targets), max(remaining_quota, 0))}",
        "",
        "## 文案样例",
    ]
    for target, text, terms in samples[:10]:
        warning = f" | 风险词: {', '.join(terms)}" if terms else ""
        lines.append(f"- {target}: {text[:100].replace(chr(10), ' ')}{warning}")
    lines.append("")
    lines.append("## 风险提示")
    if risky:
        for target, terms in risky[:20]:
            lines.append(f"- {target}: {', '.join(terms)}")
    else:
        lines.append("- 未发现内置风险词")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


async def _build_proxy(proxy_cfg: dict | None):
    """根据配置构建 Telethon 代理参数。支持 socks5/http/socks4。"""
    if not proxy_cfg:
        return None
    ptype = (proxy_cfg.get("type") or "socks5").lower()
    host = proxy_cfg.get("host", "127.0.0.1")
    port = int(proxy_cfg.get("port", 1080))
    username = proxy_cfg.get("username") or None
    password = proxy_cfg.get("password") or None
    import socks
    type_map = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}
    socks_type = type_map.get(ptype, socks.SOCKS5)
    return (socks_type, host, port, True, username, password)


async def _build_connection(proxy_cfg: dict | None):
    """根据代理类型选择 Telethon 连接器。"""
    if not proxy_cfg:
        return None
    ptype = (proxy_cfg.get("type") or "socks5").lower()
    if ptype == "http":
        return connection.ConnectionTcpMTProxyRandomizedIntermediate
    return None  # socks 走 TelegramClient 内置的 proxy 参数


async def send_messages(
    cfg: AppConfig,
    dry_run: bool = False,
    hooks: Hooks | None = None,
    stats_ref=None,
) -> SendResult:
    """
    连接 Telethon 并逐条发送私信。

    参数:
        cfg: AppConfig
        dry_run: 测试模式
        hooks: 事件回调（on_log/on_progress/should_pause/should_stop）
        stats_ref: 可选的 TaskStats 引用，实时写入计数（供 API 查询）
    """

    messages = load_messages(str(cfg.messages_path))
    targets = load_targets(str(cfg.usernames_path))

    daily_limit = cfg.daily_limit

    if stats_ref is not None:
        stats_ref.total = len(targets)
        stats_ref.daily_limit = daily_limit
        stats_ref.dry_run = dry_run or cfg.dry_run
        stats_ref.touch(action="加载发送任务", waiting=False)

    if dry_run or cfg.dry_run:
        dry_run = True
        await _log(hooks, "info", "测试模式 (dry_run=true)，不会实际发送消息")

    await _log(hooks, "info", f"连接 Session：{cfg.session_path}")
    await _activity(hooks, "连接 Telegram", waiting=False)

    # 构建代理
    proxy_params = await _build_proxy(cfg.proxy) if cfg.proxy else None
    conn_type = await _build_connection(cfg.proxy) if cfg.proxy else None
    if proxy_params:
        await _log(hooks, "info", f"使用代理：{cfg.proxy.get('type','socks5')}://{cfg.proxy.get('host')}:{cfg.proxy.get('port')}")

    client_kwargs = {"proxy": proxy_params}
    if conn_type is not None:
        client_kwargs["connection"] = conn_type

    client = TelegramClient(
        str(cfg.session_path), cfg.api_id, cfg.api_hash,
        **client_kwargs,
    )
    result = SendResult()

    try:
        try:
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
        except (OSError, ConnectionError, Exception) as e:
            error_msg = str(e)
            raise RuntimeError(
                f"网络/代理连接失败：{error_msg}\n"
                f"请严格检查以下排查项：\n"
                f"  1. 当前环境是否需要配置代理（socks5/http）？请前往 WebUI「配置」页检查设置。\n"
                f"  2. 你的本地 v2ray/clash 等代理客户端是否正常开启且允许局域网连接？\n"
                f"  3. 代理端口（如 {cfg.proxy.get('port') if cfg.proxy else '未配置'}）与软件里的 Socks5/HTTP 端口是否完全一致？\n"
                f"  4. 如果没用代理，请确认当前网络能否直连 Telegram 服务器。"
            )

        await _activity(hooks, "检查 Session 授权", waiting=False)
        if not await asyncio.wait_for(client.is_user_authorized(), timeout=AUTH_TIMEOUT):
            raise RuntimeError("Session 未授权，请先在「Session」页面登录获取 session 文件")

        await _activity(hooks, "读取账号信息", waiting=False)
        me = await asyncio.wait_for(client.get_me(), timeout=AUTH_TIMEOUT)
        account = f"{me.first_name} (@{me.username or 'N/A'})"
        account_key = f"{getattr(me, 'id', 'unknown')}:{me.username or me.first_name or 'N/A'}"
        db_path = history_path(cfg.config_dir)
        sent_today = count_success_today(db_path, account_key)
        remaining_quota = max(0, daily_limit - sent_today)
        sent_targets = successful_targets_today(db_path, account_key)
        pending_targets = [target for target in targets if target not in sent_targets]
        skipped_resume = len(targets) - len(pending_targets)
        if stats_ref is not None:
            stats_ref.account = account
            stats_ref.total = len(pending_targets)
            stats_ref.daily_limit = remaining_quota
            stats_ref.touch(action=f"已登录：{account}", waiting=False)
        await _log(hooks, "success", f"已登录：{account}")

        await _log(hooks, "info",
                   f"文案模板 {len(messages)} 条 | 目标用户 {len(targets)} 个 | "
                   f"今日已发 {sent_today} 条 | 剩余额度 {remaining_quota} 条 | "
                   f"断点跳过 {skipped_resume} 个 | 延时 {cfg.delay.min}-{cfg.delay.max}s | "
                   f"随机尾部 {'开(' + cfg.random_tail.style + ')' if cfg.random_tail.enabled else '关'}")

        if remaining_quota <= 0:
            await _log(hooks, "warn", f"账号今日发送额度已用完 ({sent_today}/{daily_limit})，安全退出")
            return result

        if dry_run:
            samples = []
            for username in pending_targets[:10]:
                template = random.choice(messages)
                preview_text = compose_message(
                    template,
                    {"username": username, "target": username, "account": account},
                    tail_enabled=cfg.random_tail.enabled,
                    tail_style=cfg.random_tail.style,
                )
                samples.append((username, preview_text, detect_risky_terms(preview_text)))
            report_path = _write_dry_run_report(cfg, account, pending_targets, samples, remaining_quota, skipped_resume)
            await _log(hooks, "info", f"Dry Run 预览报告已生成：{report_path}", "summary")

        for i, username in enumerate(pending_targets, 1):
            await _activity(hooks, "处理目标用户", current_target=username, waiting=False)
            # 停止检查
            if await _stopped(hooks):
                await _log(hooks, "warn", f"收到停止信号，跳过剩余 {len(pending_targets) - i + 1} 个目标，安全退出")
                break

            # 暂停检查（阻塞等待恢复）
            while await _paused(hooks):
                await _activity(hooks, "任务暂停中", current_target=username, waiting=True)
                if await _stopped(hooks):
                    break
                await asyncio.sleep(1)
            await _activity(hooks, "任务运行中", current_target=username, waiting=False)
            if await _stopped(hooks):
                break

            if result.sent >= remaining_quota:
                await _log(hooks, "warn", f"已达账号今日剩余额度 ({remaining_quota} 条)，安全退出")
                break

            template = random.choice(messages)
            final_text = compose_message(
                template,
                {"username": username, "target": username, "account": account},
                tail_enabled=cfg.random_tail.enabled,
                tail_style=cfg.random_tail.style,
            )
            risky_terms = detect_risky_terms(final_text)

            tag = f"[{result.sent + 1}/{remaining_quota}]"
            if risky_terms:
                await _log(hooks, "warn", f"{tag} [风险词] -> {username}: {', '.join(risky_terms)}")

            if dry_run:
                preview = final_text[:60] + ("..." if len(final_text) > 60 else "")
                await _log(hooks, "info", f"{tag} [预览] -> {username}  文案: {preview}")
                result.sent += 1
                if stats_ref is not None:
                    stats_ref.sent = result.sent
                    stats_ref.touch(action=f"预览 {username}", current_target=username, waiting=False)
                if hooks and hooks.get("on_progress"):
                    await hooks["on_progress"]()
                continue

            try:
                await _activity(hooks, "发送消息中", current_target=username, waiting=False)
                await asyncio.wait_for(client.send_message(username, final_text), timeout=SEND_TIMEOUT)
                result.sent += 1
                _record_history_safe(db_path, account_key, username, "success", message=final_text)
                if stats_ref is not None:
                    stats_ref.sent = result.sent
                    stats_ref.touch(action=f"发送成功 {username}", current_target=username, waiting=False)
                await _log(hooks, "success", f"{tag} [成功] -> {username}")
                if hooks and hooks.get("on_progress"):
                    await hooks["on_progress"]()

            except asyncio.TimeoutError:
                result.failed += 1
                _record_history_safe(db_path, account_key, username, "failed", NETWORK_FAILURE, final_text, "发送超时")
                if stats_ref is not None:
                    stats_ref.failed = result.failed
                    stats_ref.touch(action=f"发送超时 {username}", current_target=username, waiting=False)
                await _log(hooks, "fail", f"{tag} [发送超时] -> {username}")

            except FloodWaitError as e:
                wait_sec = e.seconds + random.randint(5, 15)
                await _activity(hooks, "Telegram 限流等待中", current_target=username, waiting=True)
                await _log(hooks, "warn", f"{tag} [限流] 稍后自动重试 -> {username}")
                # 分段等待以便响应停止
                elapsed = 0
                while elapsed < wait_sec:
                    if await _stopped(hooks):
                        break
                    await _activity(hooks, "Telegram 限流等待中", current_target=username, waiting=True)
                    step = min(FLOOD_WAIT_STEP, wait_sec - elapsed)
                    await asyncio.sleep(step)
                    elapsed += step
                if await _stopped(hooks):
                    await _activity(hooks, "任务已停止", current_target=username, waiting=False)
                    break
                try:
                    await _activity(hooks, "限流后重试发送", current_target=username, waiting=False)
                    await asyncio.wait_for(client.send_message(username, final_text), timeout=SEND_TIMEOUT)
                    result.sent += 1
                    _record_history_safe(db_path, account_key, username, "success", message=final_text)
                    if stats_ref is not None:
                        stats_ref.sent = result.sent
                        stats_ref.touch(action=f"重试成功 {username}", current_target=username, waiting=False)
                    await _log(hooks, "success", f"{tag} [重试成功] -> {username}")
                except asyncio.TimeoutError:
                    result.failed += 1
                    _record_history_safe(db_path, account_key, username, "failed", NETWORK_FAILURE, final_text, "重试发送超时")
                    if stats_ref is not None:
                        stats_ref.failed = result.failed
                        stats_ref.touch(action=f"重试发送超时 {username}", current_target=username, waiting=False)
                    await _log(hooks, "fail", f"{tag} [重试超时] -> {username}")
                except Exception as e2:
                    category = _failure_category(e2)
                    _record_history_safe(db_path, account_key, username, "failed", category, final_text, str(e2))
                    if category == PERMANENT_FAILURE and remove_target(str(cfg.usernames_path), username):
                        result.removed += 1
                        result.removed_usernames.append(username)
                        if stats_ref is not None:
                            stats_ref.removed = result.removed
                            stats_ref.removed_usernames = list(result.removed_usernames)
                        await _log(hooks, "remove", f"{tag} [剔除] 重试仍失败，已移除 -> {username}")
                    else:
                        result.failed += 1
                        if stats_ref is not None:
                            stats_ref.failed = result.failed
                        await _log(hooks, "fail", f"{tag} [重试失败:{category}] {e2} -> {username}")

            except PeerFloodError:
                _record_history_safe(db_path, account_key, username, "failed", TEMPORARY_FAILURE, final_text, "PeerFloodError")
                await _log(hooks, "fail",
                           f"PeerFloodError: 账号已被 Telegram 风控标记！建议停止并等待 24-48 小时。"
                           f"本次已发送 {result.sent} 条。")
                result.failed += 1
                if stats_ref is not None:
                    stats_ref.failed = result.failed
                    stats_ref.last_error = "PeerFloodError: 账号被风控标记"
                break

            except UserPrivacyRestrictedError:
                _record_history_safe(db_path, account_key, username, "failed", PERMANENT_FAILURE, final_text, "隐私限制")
                if remove_target(str(cfg.usernames_path), username):
                    result.removed += 1
                    result.removed_usernames.append(username)
                    if stats_ref is not None:
                        stats_ref.removed = result.removed
                        stats_ref.removed_usernames = list(result.removed_usernames)
                    await _log(hooks, "remove", f"{tag} [剔除] 隐私限制，已移除 -> {username}")
                else:
                    result.skipped += 1
                    if stats_ref is not None:
                        stats_ref.skipped = result.skipped
                    await _log(hooks, "skip", f"{tag} [跳过] 隐私限制 -> {username}")

            except UserNotMutualContactError:
                _record_history_safe(db_path, account_key, username, "failed", PERMANENT_FAILURE, final_text, "非互联系人")
                if remove_target(str(cfg.usernames_path), username):
                    result.removed += 1
                    result.removed_usernames.append(username)
                    if stats_ref is not None:
                        stats_ref.removed = result.removed
                        stats_ref.removed_usernames = list(result.removed_usernames)
                    await _log(hooks, "remove", f"{tag} [剔除] 非互联系人，已移除 -> {username}")
                else:
                    result.skipped += 1
                    if stats_ref is not None:
                        stats_ref.skipped = result.skipped
                    await _log(hooks, "skip", f"{tag} [跳过] 非互联系人 -> {username}")

            except RPCError as e:
                category = _failure_category(e)
                _record_history_safe(db_path, account_key, username, "failed", category, final_text, str(e))
                if category == PERMANENT_FAILURE and remove_target(str(cfg.usernames_path), username):
                    result.removed += 1
                    result.removed_usernames.append(username)
                    if stats_ref is not None:
                        stats_ref.removed = result.removed
                        stats_ref.removed_usernames = list(result.removed_usernames)
                    await _log(hooks, "remove", f"{tag} [剔除] 永久 RPC 错误，已移除 -> {username}")
                else:
                    result.failed += 1
                    if stats_ref is not None:
                        stats_ref.failed = result.failed
                    await _log(hooks, "fail", f"{tag} [失败:{category}] RPC 错误: {e} -> {username}")

            except Exception as e:
                category = _failure_category(e)
                _record_history_safe(db_path, account_key, username, "failed", category, final_text, str(e))
                result.failed += 1
                if stats_ref is not None:
                    stats_ref.failed = result.failed
                await _log(hooks, "fail", f"{tag} [失败] 未知错误: {e} -> {username}")

            # 随机延时（异步、可中断）
            if i < len(pending_targets) and result.sent < remaining_quota and not await _stopped(hooks):
                completed = await async_sleep(cfg.delay, hooks)
                if not completed:
                    await _log(hooks, "warn", "等待中被停止，退出发送循环")
                    break

        await _log(hooks, "info",
                   f"汇总：成功 {result.sent} | 失败 {result.failed} | 跳过 {result.skipped} | "
                   f"剔除 {result.removed} | 总目标 {len(targets)}", "summary")
        if result.removed_usernames:
            await _log(hooks, "remove",
                       "已剔除无效目标：" + "、".join(result.removed_usernames), "summary")

    finally:
        await safe_disconnect(client)

    return result
