# task_manager.py - 任务状态管理单例：控制启停/暂停、维护实时统计

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum

from event_bus import bus


class TaskState(str, Enum):
    IDLE = "idle"            # 空闲，未运行
    RUNNING = "running"      # 发送中
    PAUSED = "paused"        # 已暂停
    STOPPING = "stopping"    # 正在停止（等待安全断开）
    FINISHED = "finished"    # 已完成


@dataclass
class TaskStats:
    """实时发送统计。"""
    state: TaskState = TaskState.IDLE
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    removed: int = 0
    removed_usernames: list[str] = field(default_factory=list)
    total: int = 0           # 本次任务目标总数
    daily_limit: int = 0     # 本次任务上限
    dry_run: bool = False
    account: str = ""        # 已登录账号 @username
    last_error: str = ""     # 最近一次致命错误（PeerFlood 等）
    started_at: float = 0.0
    last_heartbeat: float = 0.0
    last_action: str = ""
    current_target: str = ""
    waiting: bool = False

    def progress(self) -> float:
        if self.daily_limit <= 0:
            return 0.0
        return round(self.sent / self.daily_limit, 4)

    def touch(
        self,
        action: str | None = None,
        current_target: str | None = None,
        waiting: bool | None = None,
    ) -> None:
        self.last_heartbeat = time.time()
        if action is not None:
            self.last_action = action
        if current_target is not None:
            self.current_target = current_target
        if waiting is not None:
            self.waiting = waiting

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "sent": self.sent,
            "failed": self.failed,
            "skipped": self.skipped,
            "removed": self.removed,
            "removed_usernames": list(self.removed_usernames),
            "total": self.total,
            "daily_limit": self.daily_limit,
            "dry_run": self.dry_run,
            "account": self.account,
            "progress": self.progress(),
            "last_error": self.last_error,
            "started_at": self.started_at,
            "last_heartbeat": self.last_heartbeat,
            "last_action": self.last_action,
            "current_target": self.current_target,
            "waiting": self.waiting,
        }


class TaskManager:
    """
    全局任务管理单例。

    - 持有 asyncio.Event 作为 pause/stop 信号（set=已请求）。
    - 为 sender 提供 hooks（on_log/on_progress/should_pause/should_stop）。
    - 暴露 start/pause/resume/stop 供 API 层调用。
    - 通过 event_bus 推送状态变更与日志。
    """

    def __init__(self):
        self.stats = TaskStats()
        self.pause_event = asyncio.Event()      # set() 表示请求暂停
        self.stop_event = asyncio.Event()       # set() 表示请求停止
        self._task: asyncio.Task | None = None  # 当前运行的发送协程

    # ── 状态查询 ──────────────────────────────
    def is_running(self) -> bool:
        return self.stats.state in (TaskState.RUNNING, TaskState.PAUSED, TaskState.STOPPING)

    # ── hooks（供 sender 使用）────────────────
    async def hook_on_log(self, level: str, message: str, category: str = "log") -> None:
        self.stats.touch(action=message)
        await bus.publish(level, message, category)

    async def hook_on_progress(self) -> None:
        self.stats.touch(action=self._progress_text())
        await bus.publish("info", self._progress_text(), "progress")

    def _progress_text(self) -> str:
        return f"进度 {self.stats.sent}/{self.stats.daily_limit}（成功 {self.stats.sent} 失败 {self.stats.failed} 跳过 {self.stats.skipped} 剔除 {self.stats.removed}）"

    async def hook_should_pause(self) -> bool:
        return self.pause_event.is_set()

    async def hook_should_stop(self) -> bool:
        return self.stop_event.is_set()

    async def hook_on_activity(
        self,
        action: str | None = None,
        current_target: str | None = None,
        waiting: bool | None = None,
    ) -> None:
        self.stats.touch(action=action, current_target=current_target, waiting=waiting)

    def hooks(self) -> dict:
        return {
            "on_log": self.hook_on_log,
            "on_progress": self.hook_on_progress,
            "on_activity": self.hook_on_activity,
            "should_pause": self.hook_should_pause,
            "should_stop": self.hook_should_stop,
        }

    # ── 任务生命周期 ──────────────────────────
    async def start(self, coro) -> bool:
        """启动一个发送协程。已运行则拒绝。"""
        if self.is_running():
            return False
        # 重置状态
        now = time.time()
        self.stats = TaskStats(state=TaskState.RUNNING, started_at=now, last_heartbeat=now, last_action="任务启动")
        self.pause_event.clear()
        self.stop_event.clear()
        bus.clear_history()
        self._task = asyncio.create_task(self._run(coro))
        await bus.publish("info", "任务已启动", "status")
        return True

    async def _run(self, coro):
        """包装发送协程，捕获异常并收尾。"""
        try:
            await coro
        except asyncio.CancelledError:
            self.stats.touch(action="任务被取消", waiting=False)
            await bus.publish("warn", "任务被取消", "status")
        except Exception as e:
            self.stats.last_error = str(e)
            self.stats.state = TaskState.FINISHED
            self.stats.touch(action=f"任务异常终止：{e}", waiting=False)
            await bus.publish("fail", f"任务异常终止：{e}", "status")
            return
        finally:
            if self.stats.state != TaskState.FINISHED:
                self.stats.state = TaskState.FINISHED
            self.stats.touch(action="任务结束", waiting=False)
            self._task = None
            await bus.publish("info", f"任务结束，状态：{self.stats.state.value}", "status")
            await bus.publish("info", self._progress_text(), "summary")

    async def pause(self) -> bool:
        if self.stats.state != TaskState.RUNNING:
            return False
        self.pause_event.set()
        self.stats.state = TaskState.PAUSED
        self.stats.touch(action="已请求暂停", waiting=False)
        await bus.publish("warn", "已请求暂停（当前消息发送完成后生效）", "status")
        return True

    async def resume(self) -> bool:
        if self.stats.state != TaskState.PAUSED:
            return False
        self.pause_event.clear()
        self.stats.state = TaskState.RUNNING
        self.stats.touch(action="已恢复发送", waiting=False)
        await bus.publish("info", "已恢复发送", "status")
        return True

    async def stop(self) -> bool:
        if not self.is_running():
            return False
        self.stop_event.set()
        self.pause_event.clear()  # 解除暂停以便能检查到 stop
        self.stats.state = TaskState.STOPPING
        self.stats.touch(action="已请求停止", waiting=False)
        await bus.publish("warn", "已请求停止（当前消息处理完成后安全退出）", "status")
        return True


# 全局单例
manager = TaskManager()
