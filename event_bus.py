# event_bus.py - 事件总线：采集日志/进度事件，供 SSE 端点消费

import asyncio
import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class LogEvent:
    """单条结构化事件，序列化为 SSE 数据。"""
    ts: float                           # 时间戳（time.time()）
    level: str                          # info | success | fail | warn | skip | remove | wait
    message: str                        # 可读文本
    category: str = "log"               # log | progress | status | summary

    def to_sse(self) -> str:
        return f"data: {json.dumps(asdict(self), ensure_ascii=False)}\n\n"


class EventBus:
    """
    事件总线单例。

    - publish(): 后台发送逻辑调用，写入历史缓冲 + 唤醒所有订阅者队列。
    - subscribe(): SSE 端点调用，返回一个 asyncio.Queue，实时接收新事件，
      并先补发最近 N 条历史事件（保证刷新页面不丢上下文）。
    """

    def __init__(self, history_size: int = 500):
        self._subscribers: list[asyncio.Queue] = []
        self._history: deque[LogEvent] = deque(maxlen=history_size)
        self._lock = asyncio.Lock()

    async def publish(self, level: str, message: str, category: str = "log") -> None:
        """发布一条事件到所有订阅者。"""
        event = LogEvent(ts=time.time(), level=level, message=message, category=category)
        # 历史缓冲在锁内更新，避免补发与新增交错
        async with self._lock:
            self._history.append(event)
            dead = [q for q in self._subscribers if q.full()]
            for q in dead:
                # 队列满则丢弃最旧一条，保持实时性
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    async def subscribe(self) -> asyncio.Queue:
        """订阅事件流。返回的队列已预填历史事件。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        async with self._lock:
            # 先补发历史
            for ev in self._history:
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    break
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        """取消订阅。"""
        async with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def clear_history(self) -> None:
        """清空历史缓冲（新一轮任务开始时调用）。"""
        self._history.clear()


# 全局单例
bus = EventBus()
