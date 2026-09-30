"""The shared board: values one session posts and another session takes.

A step's "post" puts values on the board under a name, and a later step in any session
reads one with {board:<name>}. Each value is taken once, oldest first, so two sessions
never get the same one. A node that reads a board is locked until the board has a value.
The board lives in one process: when a swarm runs on several machines, each has its own.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any


class Board:
    def __init__(self):
        self._values: dict[str, deque[Any]] = {}
        self._lock = threading.Lock()

    def post(self, name: str, value: Any) -> None:
        with self._lock:
            self._values.setdefault(name, deque()).append(value)

    def has(self, name: str) -> bool:
        with self._lock:
            return bool(self._values.get(name))

    def take(self, name: str) -> Any | None:
        """The oldest value under name, now removed, or None when there is none."""
        with self._lock:
            values = self._values.get(name)
            return values.popleft() if values else None

    def give_back(self, name: str, value: Any) -> None:
        """Return a taken value to the front, for a step that could not be sent."""
        with self._lock:
            self._values.setdefault(name, deque()).appendleft(value)

    def count(self, name: str) -> int:
        with self._lock:
            return len(self._values.get(name, ()))
