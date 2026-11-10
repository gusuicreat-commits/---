"""进程内事件缓冲与跨线程订阅投递。"""

import asyncio
import logging
import secrets
import threading
from collections import deque

from app.timeutil import now_bj

BOOT: str = secrets.token_hex(4)


class EventBus:
    def __init__(self, maxlen: int = 1000):
        self._events = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._last_id = 0
        self._subscribers = {}

    def publish(self, type: str, data: dict, source: str | None = None) -> dict:
        event = {}
        try:
            with self._lock:
                event = {"id": self._last_id + 1, "boot": BOOT,
                         "ts": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
                         "type": type, "source": source if source in ("phone", "screen") else None,
                         "data": data}
                self._events.append(event)
                self._last_id = event["id"]
                # 锁内按发布顺序安排回调，防止并发发布打乱订阅顺序。
                for q, loop in list(self._subscribers.items()):
                    try:
                        loop.call_soon_threadsafe(self._deliver, q, event)
                    except Exception:
                        self._subscribers.pop(q, None)
                        logging.exception("事件订阅投递失败")
        except Exception:
            logging.exception("事件发布失败")
        return event

    def _deliver(self, q, event):
        try:
            q.put_nowait(event)
        except Exception:
            self.unsubscribe(q)
            logging.exception("事件订阅队列不可用")

    def since(self, after: int, limit: int = 200) -> list[dict]:
        with self._lock:
            return [event for event in self._events if event["id"] > after][:max(0, limit)]

    def last_id(self) -> int:
        with self._lock:
            return self._last_id

    async def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers[q] = loop
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            self._subscribers.pop(q, None)


bus = EventBus()
