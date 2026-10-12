"""A stand-in for api.github.com: canned responses by method and path, every request recorded."""

from __future__ import annotations

import json
import threading
from collections import deque
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
    """Answers each request with the latest responses registered for its method and path.

    The query string plays no part in matching. Anything unregistered gets GitHub's 404.
    A GraphQL request answered by :meth:`on_graphql` is not matched by its path.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._responses: dict[tuple[str, str], deque[tuple[int, Any]]] = {}
        self._graphql: list[tuple[str, str, Any]] = []
        self.requests: list[RecordedRequest] = []

    def on(self, method: str, path: str, *, json: Any = None, status: int = 200) -> None:
        self.on_each(method, path, json=[json], status=status)

    def on_each(self, method: str, path: str, *, json: list[Any], status: int = 200) -> None:
        """Answer successive requests with each of *json* in turn, repeating the last."""
        self.on_sequence(method, path, *((status, body) for body in json))

    def on_sequence(self, method: str, path: str, *responses: tuple[int, Any]) -> None:
        """Answer successive requests with each ``(status, json)`` in turn, repeating the last."""
        if not responses:
            raise ValueError("on_sequence needs at least one response")
        with self._lock:
            self._responses[(method.upper(), path)] = deque(responses)

    def on_graphql(self, repo: str, containing: str, *, json: Any) -> None:
        """Answer GraphQL queries about *repo* whose text contains *containing* with *json*.

        Repositories read concurrently share the one /graphql path, so their queries are
        told apart by the repository they name. The latest registration that matches wins.
        """
        with self._lock:
            self._graphql.append((repo, containing, json))

    def requests_to(self, method: str, path: str) -> list[RecordedRequest]:
        with self._lock:
            return [r for r in self.requests if r.method == method.upper() and r.path == path]

    def automerge_requests(self) -> list[Any]:
        """The bodies of the GraphQL requests that enable automerge on a pull request."""
        return [
            request.body
            for request in self.requests_to("POST", "/graphql")
            if "enablePullRequestAutoMerge" in (request.body or {}).get("query", "")
        ]

    def reset(self) -> None:
        with self._lock:
            self._responses.clear()
            self._graphql.clear()
            self.requests.clear()

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        with self._lock:
            self.requests.append(
                RecordedRequest(request.method, path, dict(request.url.params), body)
            )
            graphql = self._graphql_answer(request.method, path, body)
            if graphql is not None:
                return httpx.Response(200, json=graphql)
            queued = self._responses.get((request.method, path), deque([(404, NOT_FOUND)]))
            status, payload = queued.popleft() if len(queued) > 1 else queued[0]
        return httpx.Response(status, json=payload)

    def _graphql_answer(self, method: str, path: str, body: Any) -> Any:
        if method != "POST" or path != "/graphql" or not isinstance(body, dict):
            return None
        variables = body.get("variables") or {}
        repo = f"{variables.get('owner')}/{variables.get('name')}"
        query = body.get("query") or ""
        for registered, containing, payload in reversed(self._graphql):
            if registered == repo and containing in query:
                return payload
        return None
