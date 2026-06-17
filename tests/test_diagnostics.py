"""诊断与长运行保护相关测试。"""
import asyncio
import time
from unittest.mock import AsyncMock

import pytest

import app as app_module
import task_manager as task_manager_module
from event_bus import EventBus
from task_manager import TaskManager


@pytest.mark.asyncio
async def test_event_bus_clear_history_is_locked():
    bus = EventBus()
    await bus.publish("info", "one")
    q = await bus.subscribe()
    assert not q.empty()

    await bus.clear_history()

    q2 = await bus.subscribe()
    assert q2.empty()


def test_task_manager_dump_diagnostics_writes_stack(tmp_path, monkeypatch):
    monkeypatch.setattr(task_manager_module, "REPORTS_DIR", tmp_path)
    manager = TaskManager()
    manager.stats.touch(action="测试诊断")

    path = manager.dump_diagnostics("unit test")

    content = (tmp_path / "thread_stack.txt").read_text(encoding="utf-8")
    assert path.endswith("thread_stack.txt")
    assert "Reason: unit test" in content
    assert "=== Python Threads ===" in content
    assert "=== Asyncio Tasks ===" in content


@pytest.mark.asyncio
async def test_cleanup_pending_logins_closes_expired(monkeypatch):
    client = AsyncMock()
    app_module.PENDING_LOGINS.clear()
    app_module.PENDING_LOGINS["expired"] = {
        "client": client,
        "created_at": time.time() - app_module.PENDING_LOGIN_TTL - 1,
    }

    await app_module._cleanup_pending_logins()

    assert "expired" not in app_module.PENDING_LOGINS
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_login_client_connect_timeout(tmp_path, monkeypatch):
    cfg = type("Cfg", (), {"api_id": 1, "api_hash": "x", "proxy": None})()
    client = AsyncMock()

    async def never_connect():
        await asyncio.sleep(1)

    client.connect.side_effect = never_connect
    monkeypatch.setattr(app_module, "LOGIN_CONNECT_TIMEOUT", 0.01)
    monkeypatch.setattr(app_module, "TelegramClient", lambda *args, **kwargs: client)

    with pytest.raises(asyncio.TimeoutError):
        await app_module._build_login_client(cfg, tmp_path / "s")
