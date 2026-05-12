"""GitHub API client using server-side PAT authentication.

Real implementation in issue #3. This stub exposes the interface that
tools/requirements.py will call.
"""
from __future__ import annotations

from settings import settings


class GitHubClient:
    """PAT-authenticated client for the GitHub REST API."""

    def __init__(self) -> None:
        self._pat = settings.github_pat

    async def list_files(self, repo: str, path: str) -> list[dict]:
        raise NotImplementedError("Implemented in issue #3")

    async def get_file(self, repo: str, path: str) -> dict:
        raise NotImplementedError("Implemented in issue #3")

    async def create_or_update_file(
        self, repo: str, path: str, content: str, message: str, sha: str | None = None
    ) -> dict:
        raise NotImplementedError("Implemented in issue #3")

    async def delete_file(self, repo: str, path: str, message: str, sha: str) -> None:
        raise NotImplementedError("Implemented in issue #3")

    async def create_issue(
        self, repo: str, title: str, body: str, labels: list[str] | None = None
    ) -> dict:
        raise NotImplementedError("Implemented in issue #3")


github_client = GitHubClient()
