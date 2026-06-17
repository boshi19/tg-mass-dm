"""test_scheduler.py - 定时和延时模块的 pytest 测试"""
import pytest
from unittest.mock import patch, MagicMock
from config import ScheduleConfig, DelayConfig
from scheduler import wait_until_scheduled, random_sleep


class TestRandomSleep:
    """测试微观随机延时"""

    def test_sleep_called_within_range(self):
        delay = DelayConfig(min=1.0, max=3.0)
        with patch("scheduler.time.sleep") as mock_sleep:
            random_sleep(delay)
            mock_sleep.assert_called_once()
            actual_wait = mock_sleep.call_args[0][0]
            # 带 jitter 范围 [0.85, 3.45]
            assert 0.5 <= actual_wait <= 5.0

    def test_long_sleep_is_chunked_without_countdown_logs(self):
        delay = DelayConfig(min=130.0, max=130.0)
        with patch("scheduler.time.sleep", side_effect=lambda s: None):
            with patch("builtins.print") as mock_print:
                random_sleep(delay)
                assert mock_print.call_count == 0

    def test_short_sleep_no_countdown(self):
        delay = DelayConfig(min=3.0, max=3.0)
        with patch("scheduler.time.sleep", side_effect=lambda s: None):
            with patch("builtins.print") as mock_print:
                random_sleep(delay)
                countdown_calls = [c for c in mock_print.call_args_list 
                                   if isinstance(c[0][0], str) and "剩余等待" in c[0][0]]
                assert len(countdown_calls) == 0


class TestWaitUntilScheduled:
    """测试宏观定时"""

    def test_no_schedule_returns_immediately(self):
        schedule = ScheduleConfig(start_time=None)
        with patch("scheduler.time.sleep") as mock_sleep:
            wait_until_scheduled(schedule)
            mock_sleep.assert_not_called()

    def test_past_time_no_wait(self, monkeypatch):
        """已过今日时间且 wait_if_past=false 时不等待"""
        import datetime
        from zoneinfo import ZoneInfo

        schedule = ScheduleConfig(start_time="03:00", timezone="Asia/Shanghai", wait_if_past=False)

        # 模拟当前时间为 10:00（已过 03:00）
        fake_now = datetime.datetime(2026, 6, 14, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fake_now
            mock_dt.timedelta = datetime.timedelta
            with patch("scheduler.time.sleep") as mock_sleep:
                wait_until_scheduled(schedule)
                mock_sleep.assert_not_called()

    def test_past_time_with_wait(self, monkeypatch):
        """已过今日时间且 wait_if_past=true 时等待到明天"""
        import datetime
        from zoneinfo import ZoneInfo

        schedule = ScheduleConfig(start_time="03:00", timezone="Asia/Shanghai", wait_if_past=True)

        fake_now = datetime.datetime(2026, 6, 14, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = fake_now
            mock_dt.timedelta = datetime.timedelta
            with patch("scheduler.time.sleep") as mock_sleep:
                wait_until_scheduled(schedule)
                mock_sleep.assert_called_once()
