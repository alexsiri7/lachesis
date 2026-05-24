"""Unit tests for mcp/github.py using pytest-httpx to mock httpx."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from github import (
    add_issue_dependency,
    add_to_project,
    create_branch,
    create_file,
    create_issue,
    create_pull_request,
    get_branch_sha,
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


async def test_create_file_includes_branch_when_provided(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"content": {}, "commit": {}})
    await create_file("owner/repo", "req/new.md", "content", "msg", branch="feature-branch")
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["branch"] == "feature-branch"


async def test_create_file_omits_branch_when_not_provided(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"content": {}, "commit": {}})
    await create_file("owner/repo", "req/new.md", "content", "msg")
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "branch" not in body


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
            "node_id": "I_kwDOabc",
        },
    )
    result = await create_issue("owner/repo", "Fix bug", "body text")
    assert result == {
        "id": 1001,
        "number": 42,
        "title": "Fix bug",
        "url": "https://github.com/owner/repo/issues/42",
        "node_id": "I_kwDOabc",
    }


async def test_create_issue_omits_labels_when_none(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=201,
        json={"id": 500, "number": 1, "title": "T", "html_url": "http://x", "node_id": "I_1"},
    )
    await create_issue("owner/repo", "T", "body", labels=None)
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "labels" not in body


async def test_create_issue_includes_labels_when_provided(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=201,
        json={"id": 501, "number": 2, "title": "T", "html_url": "http://x", "node_id": "I_2"},
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


# ---------------------------------------------------------------------------
# add_to_project
# ---------------------------------------------------------------------------

_GRAPHQL_URL = "https://api.github.com/graphql"


async def test_add_to_project_sends_correct_mutation(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"addProjectV2ItemById": {"item": {"id": "PVTI_abc"}}}},
    )
    await add_to_project("PVT_kwDO123", "I_kwDOabc")
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert body["variables"] == {"projectId": "PVT_kwDO123", "contentId": "I_kwDOabc"}
    assert "addProjectV2ItemById" in body["query"]


async def test_add_to_project_raises_on_http_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, status_code=401, json={"message": "bad credentials"}
    )
    with pytest.raises(httpx.HTTPStatusError):
        await add_to_project("PVT_kwDO123", "I_kwDOabc")


async def test_add_to_project_logs_graphql_errors(httpx_mock: HTTPXMock, caplog) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"errors": [{"message": "NOT_FOUND"}]},
    )
    import logging

    with caplog.at_level(logging.WARNING, logger="github"):
        await add_to_project("PVT_kwDO123", "I_kwDObad")
    assert any("graphql errors" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# get_branch_sha
# ---------------------------------------------------------------------------


async def test_get_branch_sha_returns_sha(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"object": {"sha": "abc123"}})
    result = await get_branch_sha("owner/repo", "main")
    assert result == "abc123"


async def test_get_branch_sha_raises_on_404(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=404)
    with pytest.raises(FileNotFoundError):
        await get_branch_sha("owner/repo", "nonexistent")


# ---------------------------------------------------------------------------
# create_branch
# ---------------------------------------------------------------------------


async def test_create_branch_posts_correct_ref(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=201, json={})
    await create_branch("owner/repo", "lachesis/req-001-my-feat", "abc123")
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["ref"] == "refs/heads/lachesis/req-001-my-feat"
    assert body["sha"] == "abc123"


async def test_create_branch_raises_on_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=422, json={"message": "Reference already exists"})
    with pytest.raises(httpx.HTTPStatusError):
        await create_branch("owner/repo", "lachesis/req-001-existing", "abc123")


# ---------------------------------------------------------------------------
# create_pull_request
# ---------------------------------------------------------------------------


async def test_create_pull_request_returns_number_and_url(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=201,
        json={"number": 7, "html_url": "https://github.com/owner/repo/pull/7"},
    )
    result = await create_pull_request(
        "owner/repo", "title", "body text", "lachesis/req-001-feat", "main"
    )
    assert result == {"number": 7, "url": "https://github.com/owner/repo/pull/7"}


async def test_create_pull_request_sends_correct_payload(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=201,
        json={"number": 1, "html_url": "https://github.com/owner/repo/pull/1"},
    )
    await create_pull_request("owner/repo", "My PR", "desc", "feature-branch", "main")
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["title"] == "My PR"
    assert body["head"] == "feature-branch"
    assert body["base"] == "main"


async def test_create_pull_request_raises_on_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=422, json={"message": "Validation Failed"})
    with pytest.raises(httpx.HTTPStatusError):
        await create_pull_request("owner/repo", "title", "body", "branch", "main")
