"""GitHub API client — module-level async functions using server-side PAT."""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from settings import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.github.com"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"token {settings.github_pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def list_files(repo: str, path: str) -> list[dict[str, Any]]:
    """List files at *path* in *repo*. Returns ``[]`` on 404."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
        )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else [data]


async def get_file(repo: str, path: str) -> tuple[str, str]:
    """Return ``(decoded_content, sha)`` for *path*. Raises :class:`FileNotFoundError` on 404."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
        )
    if resp.status_code == 404:
        raise FileNotFoundError(f"{path!r} not found in {repo!r}")
    resp.raise_for_status()
    data = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


async def create_file(
    repo: str, path: str, content: str, message: str
) -> dict[str, Any]:
    """Create a new file at *path* in *repo*."""
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.put(
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
            json={"message": message, "content": encoded},
        )
    resp.raise_for_status()
    return resp.json()


async def update_file(
    repo: str, path: str, content: str, sha: str, message: str
) -> dict[str, Any]:
    """Update an existing file at *path* (requires current *sha*)."""
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.put(
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
            json={"message": message, "content": encoded, "sha": sha},
        )
    resp.raise_for_status()
    return resp.json()


async def delete_file(repo: str, path: str, sha: str, message: str) -> None:
    """Delete the file at *path* (requires current *sha*)."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.delete(
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
            json={"message": message, "sha": sha},
        )
    resp.raise_for_status()


async def create_issue(
    repo: str, title: str, body: str, labels: list[str] | None = None
) -> dict[str, Any]:
    """Create an issue and return ``{number, title, url}``."""
    payload: dict[str, Any] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            f"{_BASE_URL}/repos/{repo}/issues",
            headers=_headers(),
            json=payload,
        )
    resp.raise_for_status()
    data = resp.json()
    return {"number": data["number"], "title": data["title"], "url": data["html_url"]}
