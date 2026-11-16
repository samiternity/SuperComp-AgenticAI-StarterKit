"""Per-run event log. The screen and the portal read it live or replay it afterwards."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any


class EventLog:
    def __init__(self, run_id: str, path: Path | None = None):
        self.run_id = run_id
        self.events: list[dict[str, Any]] = []
        self.started = time.monotonic()
        self.path = path
        self._subscribers: set[asyncio.Queue] = set()
        self.closed = False

    def emit(self, type_: str, **data: Any) -> dict[str, Any]:
        event = {
            "seq": len(self.events) + 1,
            "t": round(time.monotonic() - self.started, 3),
            "type": type_,
            "run_id": self.run_id,
            **data,
        }
        self.events.append(event)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        for queue in list(self._subscribers):
            queue.put_nowait(event)
        return event

    def since(self, seq: int) -> list[dict[str, Any]]:
        return self.events[seq:]

    def close(self) -> None:
        self.closed = True
        for queue in list(self._subscribers):
            queue.put_nowait(None)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        if self.closed:
            queue.put_nowait(None)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
