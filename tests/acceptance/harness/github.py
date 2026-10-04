"""A stand-in for api.github.com: canned responses by method and path, every request recorded."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

import httpx

NOT_FOUND = {"message": "Not Found", "status": "404"}


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    params: dict[str, str]
    body: Any


class FakeGitHub:
    """Answers each request with the latest response registered for its method and path.

    The query string plays no part in matching. Anything unregistered gets GitHub's 404.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._responses: dict[tuple[str, str], tuple[int, Any]] = {}
        self.requests: list[RecordedRequest] = []

    def on(self, method: str, path: str, *, json: Any = None, status: int = 200) -> None:
        with self._lock:
            self._responses[(method.upper(), path)] = (status, json)

    def requests_to(self, method: str, path: str) -> list[RecordedRequest]:
        with self._lock:
            return [r for r in self.requests if r.method == method.upper() and r.path == path]

    def reset(self) -> None:
        with self._lock:
            self._responses.clear()
            self.requests.clear()

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        with self._lock:
            self.requests.append(
                RecordedRequest(request.method, path, dict(request.url.params), body)
            )
            status, payload = self._responses.get((request.method, path), (404, NOT_FOUND))
        return httpx.Response(status, json=payload)
