"""MCP tool implementations for reading the files of a registered repository."""

from __future__ import annotations

import base64
import codecs
import mimetypes
from typing import Any

import github

from .requirements import _resolve_repo

MAX_READ_BYTES = 200 * 1024


def _decode_text(data: bytes) -> str | None:
    """Decode *data* as UTF-8, dropping a character cut in half at its end; ``None`` if binary."""
    if b"\0" in data:
        return None
    try:
        return codecs.getincrementaldecoder("utf-8")().decode(data, final=False)
    except UnicodeDecodeError:
        return None


async def read_file(repo: str, path: str, ref: str | None = None) -> dict[str, Any]:
    """Return a file's content on the default branch, or at *ref* (a branch, tag or commit).

    Returns ``{"repo", "path", "ref", "size", "truncated", "content"}``; past
    ``MAX_READ_BYTES`` only the start is returned with ``truncated`` set. A binary
    file returns ``{"repo", "path", "ref", "size", "binary": True, "type"}`` instead.
    """
    repo = await _resolve_repo(repo, require_registered=True)
    entry = await github.get_contents(repo, path, ref=ref)
    if entry is None:
        raise FileNotFoundError(f"{path!r} not found in {repo!r}" + (f" at {ref!r}" if ref else ""))
    if isinstance(entry, list) or entry.get("type") != "file":
        raise ValueError(f"{path!r} in {repo!r} is not a file")
    size = entry["size"]
    if entry.get("encoding") == "base64":
        data = base64.b64decode(entry["content"])
    else:
        data = await github.get_raw_file(repo, path, ref=ref)
    content = _decode_text(data[:MAX_READ_BYTES])
    found = {"repo": repo, "path": entry["path"], "ref": ref, "size": size}
    if content is None:
        kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return {**found, "binary": True, "type": kind}
    return {**found, "truncated": size > MAX_READ_BYTES, "content": content}


async def list_directory(repo: str, path: str = "", ref: str | None = None) -> dict[str, Any]:
    """List a directory's entries on the default branch, or at *ref*; an empty path is the root.

    Returns ``{"repo", "path", "ref", "entries": [{"name", "path", "kind"}, ...]}`` where
    ``kind`` is ``"dir"`` or ``"file"``.
    """
    repo = await _resolve_repo(repo, require_registered=True)
    path = path.strip("/")
    entries = await github.get_contents(repo, path, ref=ref)
    if entries is None:
        raise FileNotFoundError(
            f"{path or '/'!r} not found in {repo!r}" + (f" at {ref!r}" if ref else "")
        )
    if not isinstance(entries, list):
        raise ValueError(f"{path!r} in {repo!r} is a file, not a directory; use read_file")
    return {
        "repo": repo,
        "path": path,
        "ref": ref,
        "entries": [
            {
                "name": e["name"],
                "path": e["path"],
                "kind": "dir" if e["type"] == "dir" else "file",
            }
            for e in entries
        ],
    }
