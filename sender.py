# sender.py - 核心发送逻辑 (Telethon 交互)
# WebUI 版：通过 hooks 回调上报事件，支持暂停/停止，与 task_manager 协作。

import asyncio
import random
from dataclasses import dataclass, field
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
from messages import load_messages, append_random_tail
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
Hooks = dict  # {"on_log", "on_progress", "should_pause", "should_stop"}


async def safe_disconnect(client: TelegramClient) -> None:
    """安全断开 Telethon 连接，规避 set_update_state 属性的兼容错误"""
    try:
        await client.disconnect()
    except TypeError:
        try:
            await client._disconnect()
        except Exception:
            pass
        try:
            client.session.close()
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

    if dry_run or cfg.dry_run:
        dry_run = True
        await _log(hooks, "info", "测试模式 (dry_run=true)，不会实际发送消息")

    await _log(hooks, "info", f"连接 Session：{cfg.session_path}")

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
            await client.connect()
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

        if not await client.is_user_authorized():
            raise RuntimeError("Session 未授权，请先在「Session」页面登录获取 session 文件")

        me = await client.get_me()
        account = f"{me.first_name} (@{me.username or 'N/A'})"
        if stats_ref is not None:
            stats_ref.account = account
        await _log(hooks, "success", f"已登录：{account}")

        await _log(hooks, "info",
                   f"文案模板 {len(messages)} 条 | 目标用户 {len(targets)} 个 | "
                   f"每日上限 {daily_limit} 条 | 延时 {cfg.delay.min}-{cfg.delay.max}s | "
                   f"随机尾部 {'开(' + cfg.random_tail.style + ')' if cfg.random_tail.enabled else '关'}")

        for i, username in enumerate(targets, 1):
            # 停止检查
            if await _stopped(hooks):
                await _log(hooks, "warn", f"收到停止信号，跳过剩余 {len(targets) - i + 1} 个目标，安全退出")
                break

            # 暂停检查（阻塞等待恢复）
            while await _paused(hooks):
                if await _stopped(hooks):
                    break
                await asyncio.sleep(1)
            if await _stopped(hooks):
                break

            if result.sent >= daily_limit:
                await _log(hooks, "warn", f"已达每日发送上限 ({daily_limit} 条)，安全退出")
                break

            template = random.choice(messages)
            if cfg.random_tail.enabled:
                final_text = append_random_tail(template, cfg.random_tail.style)
            else:
                final_text = template

            tag = f"[{result.sent + 1}/{daily_limit}]"

            if dry_run:
                preview = final_text[:60] + ("..." if len(final_text) > 60 else "")
                await _log(hooks, "info", f"{tag} [预览] -> {username}  文案: {preview}")
                result.sent += 1
                if stats_ref is not None:
                    stats_ref.sent = result.sent
                if hooks and hooks.get("on_progress"):
                    await hooks["on_progress"]()
                continue

            try:
                await client.send_message(username, final_text)
                result.sent += 1
                if stats_ref is not None:
                    stats_ref.sent = result.sent
                await _log(hooks, "success", f"{tag} [成功] -> {username}")
                if hooks and hooks.get("on_progress"):
                    await hooks["on_progress"]()

            except FloodWaitError as e:
                wait_sec = e.seconds + random.randint(5, 15)
                await _log(hooks, "warn", f"{tag} [限流] 稍后自动重试 -> {username}")
                # 分段等待以便响应停止
                for _ in range(0, wait_sec, 5):
                    if await _stopped(hooks):
                        break
                    await asyncio.sleep(min(5, wait_sec))
                if await _stopped(hooks):
                    break
                try:
                    await client.send_message(username, final_text)
                    result.sent += 1
                    if stats_ref is not None:
                        stats_ref.sent = result.sent
                    await _log(hooks, "success", f"{tag} [重试成功] -> {username}")
                except Exception as e2:
                    if remove_target(str(cfg.usernames_path), username):
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
                        await _log(hooks, "fail", f"{tag} [重试失败] {e2} -> {username}")

            except PeerFloodError:
                await _log(hooks, "fail",
                           f"PeerFloodError: 账号已被 Telegram 风控标记！建议停止并等待 24-48 小时。"
                           f"本次已发送 {result.sent} 条。")
                result.failed += 1
                if stats_ref is not None:
                    stats_ref.failed = result.failed
                    stats_ref.last_error = "PeerFloodError: 账号被风控标记"
                break

            except UserPrivacyRestrictedError:
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
                if remove_target(str(cfg.usernames_path), username):
                    result.removed += 1
                    result.removed_usernames.append(username)
                    if stats_ref is not None:
                        stats_ref.removed = result.removed
                        stats_ref.removed_usernames = list(result.removed_usernames)
                    await _log(hooks, "remove", f"{tag} [剔除] RPC 错误，已移除 -> {username}")
                else:
                    result.failed += 1
                    if stats_ref is not None:
                        stats_ref.failed = result.failed
                    await _log(hooks, "fail", f"{tag} [失败] RPC 错误: {e} -> {username}")

            except Exception as e:
                result.failed += 1
                if stats_ref is not None:
                    stats_ref.failed = result.failed
                await _log(hooks, "fail", f"{tag} [失败] 未知错误: {e} -> {username}")

            # 随机延时（异步、可中断）
            if i < len(targets) and result.sent < daily_limit and not await _stopped(hooks):
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
