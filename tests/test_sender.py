"""test_sender.py - 核心发送逻辑模块的 pytest 测试"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path
from config import AppConfig, ScheduleConfig, DelayConfig, RandomTailConfig
from sender import send_messages, SendResult, safe_disconnect


def make_test_config(tmp_path, **overrides):
    """创建测试用 AppConfig"""
    defaults = dict(
        api_id=123456,
        api_hash="abc123def456",
        session_file="test_session",
        usernames_file="usernames.txt",
        messages_file="messages.txt",
        schedule=ScheduleConfig(start_time=None),
        delay=DelayConfig(min=0.1, max=0.2),
        random_tail=RandomTailConfig(enabled=True, style="dots"),
        daily_limit=5,
        dry_run=False,
        config_dir=tmp_path,
    )
    defaults.update(overrides)
    return AppConfig(**defaults)


class TestSendResult:
    """测试 SendResult 数据类"""

    def test_default_values(self):
        result = SendResult()
        assert result.sent == 0
        assert result.failed == 0
        assert result.skipped == 0
        assert result.removed == 0
        assert result.removed_usernames == []

    def test_custom_values(self):
        result = SendResult(sent=5, failed=1, skipped=2, removed=3, removed_usernames=["u1", "u2"])
        assert result.sent == 5
        assert result.failed == 1
        assert result.skipped == 2
        assert result.removed == 3
        assert result.removed_usernames == ["u1", "u2"]


class TestSafeDisconnect:
    """测试安全断开连接"""

    @pytest.mark.asyncio
    async def test_safe_disconnect_normal(self):
        client = AsyncMock()
        await safe_disconnect(client)
        client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_safe_disconnect_typeerror(self):
        """TypeError 时降级到 _disconnect"""
        client = AsyncMock()
        client.disconnect.side_effect = TypeError("mock error")
        client._disconnect = AsyncMock()
        await safe_disconnect(client)
        client._disconnect.assert_called_once()


class TestSendMessages:
    """测试核心发送逻辑"""

    def _setup_files(self, tmp_path):
        """创建测试用的 usernames.txt 和 messages.txt"""
        usernames = tmp_path / "usernames.txt"
        usernames.write_text("user1\nuser2", encoding="utf-8")
        messages = tmp_path / "messages.txt"
        messages.write_text("Hello\nHi", encoding="utf-8")
        return str(usernames), str(messages)

    @pytest.mark.asyncio
    async def test_dry_run_no_actual_send(self, tmp_path):
        """dry_run 模式不调用 send_message"""
        self._setup_files(tmp_path)
        cfg = make_test_config(tmp_path, dry_run=True)

        with patch("sender.TelegramClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client.is_user_authorized = AsyncMock(return_value=True)
            mock_client.get_me = AsyncMock(return_value=MagicMock(first_name="Test", username=None))
            mock_client_cls.return_value = mock_client

            result = await send_messages(cfg, dry_run=True)

            # dry_run 下不应调用 send_message
            mock_client.send_message.assert_not_called()
            assert result.sent > 0

    @pytest.mark.asyncio
    async def test_daily_limit_stops(self, tmp_path):
        """达到 daily_limit 后停止发送"""
        self._setup_files(tmp_path)
        cfg = make_test_config(tmp_path, daily_limit=1)

        with patch("sender.TelegramClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client.is_user_authorized = AsyncMock(return_value=True)
            mock_client.get_me = AsyncMock(return_value=MagicMock(first_name="Test", username=None))
            mock_client.send_message = AsyncMock()
            mock_client_cls.return_value = mock_client

            with patch("sender.random_sleep", return_value=None):
                result = await send_messages(cfg, dry_run=False)

            # 只发了一条（daily_limit=1），两个目标只发了1个
            assert result.sent <= 1

    @pytest.mark.asyncio
    async def test_peerflood_breaks_loop(self, tmp_path):
        """PeerFloodError 后立即终止"""
        self._setup_files(tmp_path)
        cfg = make_test_config(tmp_path)

        from telethon.errors import PeerFloodError

        with patch("sender.TelegramClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client.is_user_authorized = AsyncMock(return_value=True)
            mock_client.get_me = AsyncMock(return_value=MagicMock(first_name="Test", username=None))
            mock_client.send_message = AsyncMock(side_effect=PeerFloodError)
            mock_client_cls.return_value = mock_client

            with patch("sender.random_sleep", return_value=None):
                result = await send_messages(cfg, dry_run=False)

            assert result.sent == 0
            assert result.failed > 0

    @pytest.mark.asyncio
    async def test_floodwait_retry(self, tmp_path):
        """FloodWaitError 后重试"""
        self._setup_files(tmp_path)
        cfg = make_test_config(tmp_path)

        from telethon.errors import FloodWaitError

        call_count = [0]

        async def mock_send(username, text):
            call_count[0] += 1
            if call_count[0] == 1:
                raise FloodWaitError(seconds=1)
            return None  # success

        with patch("sender.TelegramClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client.is_user_authorized = AsyncMock(return_value=True)
            mock_client.get_me = AsyncMock(return_value=MagicMock(first_name="Test", username=None))
            mock_client.send_message = AsyncMock(side_effect=mock_send)
            mock_client_cls.return_value = mock_client

            with patch("sender.random_sleep", return_value=None):
                with patch("asyncio.sleep", AsyncMock()):
                    result = await send_messages(cfg, dry_run=False)

            # 第一次 floodwait + 重试成功 = 至少2次调用
            assert call_count[0] >= 2
