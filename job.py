"""后台任务状态容器（扫描 / 导出共用）。"""

from __future__ import annotations

import threading
import time
from typing import Any


class JobCancelled(Exception):
    """用户主动取消。"""


class Job:
    """线程安全的任务状态，供前端定时轮询。"""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._lock = threading.Lock()
        self.cancel_event = threading.Event()
        self._started = time.time()
        self._state: dict[str, Any] = {
            "kind": kind,
            "status": "running",      # running | done | cancelled | error
            "phase": "",
            "processed": 0,
            "total": 0,
            "percent": 0.0,
            "current": "",
            "message": "",
            "error": "",
            "elapsed_ms": 0,
            "extra": {},
        }

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            self._state.update(kwargs)
            total = self._state["total"]
            if total and self._state["status"] == "running":
                self._state["percent"] = min(99.5, self._state["processed"] / total * 100.0)
            self._state["elapsed_ms"] = int((time.time() - self._started) * 1000)

    def finish(self, status: str, **kwargs: Any) -> None:
        with self._lock:
            self._state.update(kwargs)
            self._state["status"] = status
            self._state["percent"] = 100.0 if status == "done" else self._state.get("percent", 0.0)
            self._state["elapsed_ms"] = int((time.time() - self._started) * 1000)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = dict(self._state)
            data["extra"] = dict(self._state["extra"])
        return data

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled()
