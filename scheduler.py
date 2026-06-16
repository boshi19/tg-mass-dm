# scheduler.py - 宏观定时 + 微观随机延时（同步版保留兼容，新增 async 版供 WebUI）

import asyncio
import datetime
import random
import time
from zoneinfo import ZoneInfo

from config import ScheduleConfig, DelayConfig


def wait_until_scheduled(schedule: ScheduleConfig) -> None:
    """
    如果配置了定时，在指定时间前阻塞等待。

    参数:
        schedule: ScheduleConfig
    """
    if not schedule or not schedule.start_time:
        return

    tz = ZoneInfo(schedule.timezone if schedule.timezone else "Asia/Shanghai")
    now = datetime.datetime.now(tz)
    target_time_str = schedule.start_time

    try:
        hour, minute = map(int, str(target_time_str).split(":"))
    except ValueError:
        print(f"[错误] start_time 格式无效: {target_time_str}，应为 HH:MM")
        import sys
        sys.exit(1)

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if target <= now:
        if schedule.wait_if_past:
            target += datetime.timedelta(days=1)
            print(f"[定时] 已过 {target_time_str}，将等到明天 {target.strftime('%Y-%m-%d %H:%M')}")
        else:
            print(f"[定时] 已过 {target_time_str}，立即执行（wait_if_past=false）")
            return

    wait_seconds = (target - now).total_seconds()
    print(f"[定时] 定时启动: {target.strftime('%Y-%m-%d %H:%M %Z')}，进入等待...")
    time.sleep(wait_seconds)
    print("[定时] 到达预定时间，开始执行！")


def random_sleep(delay: DelayConfig) -> None:
    """
    在 [min, max] 范围内随机等待，长间隔时每分钟打印倒计时。

    参数:
        delay: DelayConfig
    """
    d_min = delay.min if delay else 8.0
    d_max = delay.max if delay else 25.0
    wait = random.uniform(d_min, d_max)
    jitter = wait * random.uniform(-0.15, 0.15)
    actual = max(1.0, wait + jitter)

    if actual > 120:
        total = int(actual)
        while total > 0:
            step = min(60, total)
            time.sleep(step)
            total -= step
    else:
        time.sleep(actual)


# ═══════════════════════════════════════════
#  异步版（供 WebUI 使用，可暂停/停止/上报倒计时）
# ═══════════════════════════════════════════


async def async_sleep(delay: DelayConfig, hooks: dict | None = None) -> bool:
    """
    异步随机延时。长间隔时分段 await，便于响应暂停/停止。

    参数:
        delay: DelayConfig
        hooks: 可选，包含 should_pause / should_stop / on_log 协程
    返回:
        bool - True=正常等完，False=被请求停止
    """
    d_min = delay.min if delay else 8.0
    d_max = delay.max if delay else 25.0
    wait = random.uniform(d_min, d_max)
    jitter = wait * random.uniform(-0.15, 0.15)
    actual = max(1.0, wait + jitter)

    should_stop = hooks.get("should_stop") if hooks else None

    total = int(actual)
    if total > 120:
        while total > 0:
            # 检查停止
            if should_stop and await should_stop():
                return False
            step = min(10, total)  # 10 秒粒度切片，快速响应控制信号
            await asyncio.sleep(step)
            total -= step
    else:
        remaining = actual
        while remaining > 0:
            if should_stop and await should_stop():
                return False
            step = min(2.0, remaining)
            await asyncio.sleep(step)
            remaining -= step
    return True


async def async_wait_until_scheduled(schedule: ScheduleConfig, hooks: dict | None = None) -> bool:
    """
    异步版宏观定时等待。返回 True=到点/无需等待，False=被停止。

    与同步版语义一致：无 start_time 立即返回；超过时间且 wait_if_past=false 立即执行。
    """
    if not schedule or not schedule.start_time:
        return True

    tz = ZoneInfo(schedule.timezone if schedule.timezone else "Asia/Shanghai")
    now = datetime.datetime.now(tz)
    target_time_str = schedule.start_time

    try:
        hour, minute = map(int, str(target_time_str).split(":"))
    except ValueError:
        return True  # 格式错误不阻塞

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if target <= now:
        if schedule.wait_if_past:
            target += datetime.timedelta(days=1)
        else:
            return True

    on_log = hooks.get("on_log") if hooks else None
    should_stop = hooks.get("should_stop") if hooks else None

    if on_log:
        await on_log("info", f"定时启动：{target.strftime('%Y-%m-%d %H:%M')}，进入等待")

    while True:
        now = datetime.datetime.now(tz)
        if now >= target:
            return True
        if should_stop and await should_stop():
            return False
        await asyncio.sleep(5)
