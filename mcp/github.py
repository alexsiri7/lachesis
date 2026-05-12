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
        "Authorization": f"Bearer {settings.github_pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def list_files(repo: str, path: str) -> list[dict[str, Any]]:
    """List files at *path* in *repo*. Returns ``[]`` on 404.

    If *path* resolves to a single file rather than a directory, returns a
    one-element list (GitHub Contents API returns a dict in that case).
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
        )
    if resp.status_code == 404:
        return []
    if not resp.is_success:
        logger.warning(
            "github.list_files failed repo=%s path=%s status=%s",
            repo, path, resp.status_code,
        )
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
    if not resp.is_success:
        logger.warning(
            "github.get_file failed repo=%s path=%s status=%s",
            repo, path, resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        raise ValueError(f"{path!r} is a directory in {repo!r}, not a file")
    raw = data.get("content")
    if raw is None:
        raise ValueError(
            f"{path!r} in {repo!r} has no inline content "
            "(may be a submodule, symlink, or file >1 MB)"
        )
    content = base64.b64decode(raw).decode("utf-8")
    return content, data["sha"]


async def create_file(
    repo: str, path: str, content: str, message: str
) -> dict[str, Any]:
    """Create a new file at *path* in *repo*. Returns the raw GitHub Contents API response."""
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.put(
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
            json={"message": message, "content": encoded},
        )
    if not resp.is_success:
        logger.warning(
            "github.create_file failed repo=%s path=%s status=%s",
            repo, path, resp.status_code,
        )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def update_file(
    repo: str, path: str, content: str, sha: str, message: str
) -> dict[str, Any]:
    """Update an existing file at *path* (requires current *sha*).

    Returns the raw GitHub Contents API response.
    """
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.put(
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
            json={"message": message, "content": encoded, "sha": sha},
        )
    if not resp.is_success:
        logger.warning(
            "github.update_file failed repo=%s path=%s status=%s",
            repo, path, resp.status_code,
        )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def delete_file(repo: str, path: str, sha: str, message: str) -> None:
    """Delete the file at *path* (requires current *sha*)."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.delete(  # type: ignore[call-arg]
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
            json={"message": message, "sha": sha},
        )
    if not resp.is_success:
        logger.warning(
            "github.delete_file failed repo=%s path=%s status=%s",
            repo, path, resp.status_code,
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
    if not resp.is_success:
        logger.warning(
            "github.create_issue failed repo=%s status=%s",
            repo, resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    return {"number": data["number"], "title": data["title"], "url": data["html_url"]}
