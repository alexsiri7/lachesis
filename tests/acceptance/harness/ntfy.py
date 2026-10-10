"""A stand-in for ntfy.sh: every notification published to it is recorded."""

from __future__ import annotations

import threading
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Notification:
    topic: str
    title: str
    body: str
    click: str | None
    priority: str | None


class FakeNtfy:
    """Accepts every publish, or refuses every connection once made unreachable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reachable = True
        self.notifications: list[Notification] = []

    def unreachable(self) -> None:
        with self._lock:
            self._reachable = False

    def reset(self) -> None:
        with self._lock:
            self._reachable = True
            self.notifications.clear()

    def handle(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            if not self._reachable:
                raise httpx.ConnectError("ntfy is unreachable", request=request)
            self.notifications.append(
                Notification(
                    topic=request.url.path.lstrip("/"),
                    title=request.headers.get("Title", ""),
                    body=request.content.decode("utf-8"),
                    click=request.headers.get("Click"),
                    priority=request.headers.get("Priority"),
                )
            )
        return httpx.Response(200, json={"event": "message"})
