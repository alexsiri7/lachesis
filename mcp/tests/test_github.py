"""Unit tests for mcp/github.py using pytest-httpx to mock httpx."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from github import (
    add_issue_dependency,
    create_file,
    create_issue,
    get_file,
    list_files,
    update_file,
)

# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


async def test_list_files_returns_empty_on_404(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=404)
    result = await list_files("owner/repo", "requirements")
    assert result == []


async def test_list_files_returns_list_unchanged(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=[{"name": "a.md"}, {"name": "b.md"}])
    result = await list_files("owner/repo", "requirements")
    assert len(result) == 2
    assert result[0]["name"] == "a.md"


async def test_list_files_normalizes_single_file_to_list(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"name": "file.md", "sha": "abc123"})
    result = await list_files("owner/repo", "requirements/foo.md")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "file.md"


# ---------------------------------------------------------------------------
# get_file
# ---------------------------------------------------------------------------


async def test_get_file_raises_on_404(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=404)
    with pytest.raises(FileNotFoundError, match="not found in"):
        await get_file("owner/repo", "missing.md")


async def test_get_file_decodes_base64_content(httpx_mock: HTTPXMock) -> None:
    raw = "# Hello\nWorld"
    encoded = base64.b64encode(raw.encode()).decode("ascii")
    sha = "deadbeef"
    httpx_mock.add_response(json={"content": encoded, "sha": sha})
    content, returned_sha = await get_file("owner/repo", "foo.md")
    assert content == raw
    assert returned_sha == sha


async def test_get_file_handles_github_chunked_base64(httpx_mock: HTTPXMock) -> None:
    """GitHub inserts newlines every 60 chars in base64 content."""
    raw = "x" * 200
    encoded_clean = base64.b64encode(raw.encode()).decode("ascii")
    chunked = "\n".join(encoded_clean[i : i + 60] for i in range(0, len(encoded_clean), 60)) + "\n"
    httpx_mock.add_response(json={"content": chunked, "sha": "abc"})
    content, _ = await get_file("owner/repo", "big.md")
    assert content == raw


async def test_get_file_raises_on_directory_path(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=[{"name": "a.md"}, {"name": "b.md"}])
    with pytest.raises(ValueError, match="is a directory"):
        await get_file("owner/repo", "requirements")


async def test_get_file_raises_on_null_content(httpx_mock: HTTPXMock) -> None:
    """Files >1MB have content=null in the GitHub API response."""
    httpx_mock.add_response(json={"content": None, "sha": "abc", "download_url": "http://x"})
    with pytest.raises(ValueError, match="has no inline content"):
        await get_file("owner/repo", "huge.md")


# ---------------------------------------------------------------------------
# create_file
# ---------------------------------------------------------------------------


async def test_create_file_sends_base64_encoded_content(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"content": {}, "commit": {}})
    raw = "# New requirement"
    await create_file("owner/repo", "req/new.md", raw, "add file")
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert base64.b64decode(body["content"]).decode() == raw
    assert "sha" not in body  # create must NOT include sha


async def test_create_file_returns_raw_response(httpx_mock: HTTPXMock) -> None:
    expected = {"content": {"sha": "new_sha"}, "commit": {"sha": "commit_sha"}}
    httpx_mock.add_response(json=expected)
    result = await create_file("owner/repo", "req/new.md", "content", "msg")
    assert result == expected


# ---------------------------------------------------------------------------
# update_file
# ---------------------------------------------------------------------------


async def test_update_file_includes_sha_in_payload(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"content": {}, "commit": {}})
    await update_file("owner/repo", "req/existing.md", "new content", "abc123", "update")
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert body["sha"] == "abc123"


async def test_update_file_sends_base64_encoded_content(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"content": {}, "commit": {}})
    raw = "# Updated"
    await update_file("owner/repo", "req/existing.md", raw, "sha1", "update")
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert base64.b64decode(body["content"]).decode() == raw


# ---------------------------------------------------------------------------
# create_issue
# ---------------------------------------------------------------------------


async def test_create_issue_returns_mapped_shape(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=201,
        json={
            "id": 1001,
            "number": 42,
            "title": "Fix bug",
            "url": "https://api.github.com/repos/owner/repo/issues/42",
            "html_url": "https://github.com/owner/repo/issues/42",
        },
    )
    result = await create_issue("owner/repo", "Fix bug", "body text")
    assert result == {
        "id": 1001,
        "number": 42,
        "title": "Fix bug",
        "url": "https://github.com/owner/repo/issues/42",
    }


async def test_create_issue_omits_labels_when_none(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=201,
        json={"id": 500, "number": 1, "title": "T", "html_url": "http://x"},
    )
    await create_issue("owner/repo", "T", "body", labels=None)
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "labels" not in body


async def test_create_issue_includes_labels_when_provided(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=201,
        json={"id": 501, "number": 2, "title": "T", "html_url": "http://x"},
    )
    await create_issue("owner/repo", "T", "body", labels=["bug", "enhancement"])
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["labels"] == ["bug", "enhancement"]


# ---------------------------------------------------------------------------
# add_issue_dependency
# ---------------------------------------------------------------------------

_DEP_URL = "https://api.github.com/repos/owner/repo/issues/42/dependencies/blocked_by"


async def test_add_issue_dependency_sends_correct_payload(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=_DEP_URL, status_code=200, json={})
    await add_issue_dependency("owner/repo", 42, 9001)
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert body == {"issue_id": 9001}


async def test_add_issue_dependency_raises_on_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=_DEP_URL, status_code=422, json={"message": "err"})
    with pytest.raises(httpx.HTTPStatusError):
        await add_issue_dependency("owner/repo", 42, 9001)
