"""Estado em memória compartilhado entre o poller e a UI local de status."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, List


@dataclass
class ActivityEntry:
    timestamp: float
    message: str


class RuntimeState:
    def __init__(self, max_entries: int = 100):
        self._lock = threading.Lock()
        self._entries: Deque[ActivityEntry] = deque(maxlen=max_entries)

    def record(self, message: str) -> None:
        with self._lock:
            self._entries.append(ActivityEntry(timestamp=time.time(), message=message))

    def recent(self) -> List[ActivityEntry]:
        with self._lock:
            return list(self._entries)
