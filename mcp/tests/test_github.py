"""Unit tests for mcp/github.py using pytest-httpx to mock httpx."""

from __future__ import annotations

import base64
import json
import re

import httpx
import pytest
from pytest_httpx import HTTPXMock

from derive import derive
from github import (
    _headers,
    add_issue_dependency,
    add_labels,
    add_to_project,
    create_branch,
    create_file,
    create_issue,
    create_issue_comment,
    create_pull_request,
    create_repo,
    enable_automerge,
    failure_from_exception,
    find_open_pr,
    get_branch_sha,
    get_file,
    get_issue,
    get_issue_activity,
    get_issue_dependencies,
    get_merge_base,
    get_production_deployment,
    get_pull_request,
    get_pull_request_files,
    get_repo_settings,
    get_scenario_results,
    list_blocked_by_numbers,
    list_blockers,
    list_files,
    list_issue_comments,
    list_labelled_prs,
    list_lachesis_prs,
    list_open_issues,
    list_pull_request_files,
    remove_label,
    update_file,
    update_issue,
)

# ---------------------------------------------------------------------------
# _headers
# ---------------------------------------------------------------------------


def test_headers_includes_correct_github_api_version() -> None:
    headers = _headers()
    assert headers["X-GitHub-Api-Version"] == "2026-03-10"
    assert headers["Accept"] == "application/vnd.github+json"
    assert "Authorization" in headers


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


async def test_update_file_includes_branch_in_payload(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"content": {}, "commit": {}})
    await update_file("owner/repo", "req.md", "content", "sha1", "msg", branch="feature-branch")
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["branch"] == "feature-branch"


async def test_update_file_omits_branch_when_not_provided(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"content": {}, "commit": {}})
    await update_file("owner/repo", "req.md", "content", "sha1", "msg")
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "branch" not in body


# ---------------------------------------------------------------------------
# list_files — branch param
# ---------------------------------------------------------------------------


async def test_list_files_sends_ref_param_when_branch_provided(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=[])
    await list_files("owner/repo", "requirements", branch="lachesis/req-001-user-auth")
    request = httpx_mock.get_requests()[0]
    assert "ref=lachesis" in str(request.url)


async def test_list_files_omits_ref_param_when_no_branch(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=[])
    await list_files("owner/repo", "requirements")
    request = httpx_mock.get_requests()[0]
    assert "ref=" not in str(request.url)


# ---------------------------------------------------------------------------
# get_file — branch param
# ---------------------------------------------------------------------------


async def test_get_file_sends_ref_param_when_branch_provided(httpx_mock: HTTPXMock) -> None:
    raw = "# Hello"
    encoded = base64.b64encode(raw.encode()).decode("ascii")
    httpx_mock.add_response(json={"content": encoded, "sha": "abc"})
    await get_file("owner/repo", "requirements/001-auth.md", branch="lachesis/req-001-user-auth")
    request = httpx_mock.get_requests()[0]
    assert "ref=lachesis" in str(request.url)


# ---------------------------------------------------------------------------
# find_open_pr
# ---------------------------------------------------------------------------


def _open_pr(ref: str, number: int) -> dict:
    return {
        "head": {"ref": ref},
        "number": number,
        "html_url": f"https://github.com/owner/repo/pull/{number}",
        "created_at": "2026-09-01T00:00:00Z",
    }


async def test_find_open_pr_returns_matching_pr(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json=[
            _open_pr("lachesis/req-002-other", 2),
            _open_pr("lachesis/req-001-user-auth", 1),
        ]
    )
    result = await find_open_pr("owner/repo", "lachesis/req-001-")
    assert result == {
        "branch": "lachesis/req-001-user-auth",
        "pr_number": 1,
        "pr_url": "https://github.com/owner/repo/pull/1",
        "created_at": "2026-09-01T00:00:00Z",
    }


async def test_find_open_pr_returns_none_when_no_match(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=[_open_pr("lachesis/req-002-other", 2)])
    result = await find_open_pr("owner/repo", "lachesis/req-001-")
    assert result is None


async def test_find_open_pr_returns_none_when_empty(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=[])
    result = await find_open_pr("owner/repo", "lachesis/req-001-")
    assert result is None


async def test_find_open_pr_raises_on_http_error(httpx_mock: HTTPXMock) -> None:
    """An unreadable PR list must not read as "this requirement has no PR"."""
    httpx_mock.add_response(status_code=403, json={"message": "Forbidden"})
    with pytest.raises(httpx.HTTPStatusError):
        await find_open_pr("owner/repo", "lachesis/req-001-")


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
# add_labels
# ---------------------------------------------------------------------------


async def test_add_labels_posts_names_to_issue_labels(httpx_mock: HTTPXMock) -> None:
    url = "https://api.github.com/repos/owner/repo/issues/7/labels"
    httpx_mock.add_response(method="POST", url=url, json=[{"name": "spec-change"}])
    await add_labels("owner/repo", 7, ["spec-change"])
    assert json.loads(httpx_mock.get_requests()[0].content) == {"labels": ["spec-change"]}


# ---------------------------------------------------------------------------
# remove_label
# ---------------------------------------------------------------------------

_NEEDS_AUTHOR_URL = "https://api.github.com/repos/owner/repo/issues/7/labels/needs-author"


async def test_remove_label_deletes_the_named_label(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="DELETE", url=_NEEDS_AUTHOR_URL, json=[])
    await remove_label("owner/repo", 7, "needs-author")
    assert httpx_mock.get_requests()[0].method == "DELETE"


async def test_remove_label_tolerates_a_label_already_gone(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="DELETE", url=_NEEDS_AUTHOR_URL, status_code=404)
    await remove_label("owner/repo", 7, "needs-author")


async def test_remove_label_raises_on_other_failures(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="DELETE", url=_NEEDS_AUTHOR_URL, status_code=500)
    with pytest.raises(httpx.HTTPStatusError):
        await remove_label("owner/repo", 7, "needs-author")


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
        json={
            "number": 7,
            "html_url": "https://github.com/owner/repo/pull/7",
            "node_id": "PR_abc123",
            "created_at": "2026-09-14T10:00:00Z",
        },
    )
    result = await create_pull_request(
        "owner/repo", "title", "body text", "lachesis/req-001-feat", "main"
    )
    assert result == {
        "number": 7,
        "url": "https://github.com/owner/repo/pull/7",
        "node_id": "PR_abc123",
        "created_at": "2026-09-14T10:00:00Z",
    }


async def test_create_pull_request_sends_correct_payload(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=201,
        json={
            "number": 1,
            "html_url": "https://github.com/owner/repo/pull/1",
            "node_id": "PR_def456",
        },
    )
    await create_pull_request("owner/repo", "My PR", "desc", "feature-branch", "main")
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["title"] == "My PR"
    assert body["body"] == "desc"
    assert body["head"] == "feature-branch"
    assert body["base"] == "main"


async def test_create_pull_request_raises_on_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=422, json={"message": "Validation Failed"})
    with pytest.raises(httpx.HTTPStatusError):
        await create_pull_request("owner/repo", "title", "body", "branch", "main")


# ---------------------------------------------------------------------------
# get_issue
# ---------------------------------------------------------------------------


async def test_get_issue_returns_all_fields(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json={
            "id": 100,
            "number": 5,
            "title": "Test issue",
            "state": "open",
            "body": "Some description",
            "assignees": [{"login": "bob"}],
            "labels": [{"name": "enhancement"}],
            "pull_request": {"html_url": "https://github.com/o/r/pull/3"},
            "html_url": "https://github.com/o/r/issues/5",
        }
    )
    result = await get_issue("owner/repo", 5)
    assert result is not None
    assert result["number"] == 5
    assert result["id"] == 100
    assert result["title"] == "Test issue"
    assert result["state"] == "open"
    assert result["body"] == "Some description"
    assert result["assignees"] == ["bob"]
    assert result["labels"] == ["enhancement"]
    assert result["linked_pr"] == "https://github.com/o/r/pull/3"
    assert result["url"] == "https://github.com/o/r/issues/5"


async def test_get_issue_returns_none_on_404(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=404)
    result = await get_issue("owner/repo", 999)
    assert result is None


async def test_get_issue_null_body_coerced_to_empty_string(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json={
            "id": 1,
            "number": 1,
            "title": "T",
            "state": "open",
            "body": None,
            "assignees": [],
            "labels": [],
            "html_url": "https://github.com/o/r/issues/1",
        }
    )
    result = await get_issue("owner/repo", 1)
    assert result is not None
    assert result["body"] == ""


async def test_get_issue_no_linked_pr_returns_none(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json={
            "id": 1,
            "number": 1,
            "title": "T",
            "state": "open",
            "body": "",
            "assignees": [],
            "labels": [],
            "html_url": "https://github.com/o/r/issues/1",
        }
    )
    result = await get_issue("owner/repo", 1)
    assert result is not None
    assert result["linked_pr"] is None


# ---------------------------------------------------------------------------
# get_pull_request
# ---------------------------------------------------------------------------

_PULL_JSON = {
    "number": 7,
    "title": "Add the thing",
    "html_url": "https://github.com/o/r/pull/7",
    "state": "closed",
    "merged": True,
    "merged_at": "2026-09-01T10:00:00Z",
    "mergeable": None,
    "mergeable_state": "unknown",
    "head": {"ref": "feature/thing"},
    "base": {"ref": "main"},
    "draft": False,
    "labels": [{"name": "spec-change"}],
}


async def test_get_pull_request_returns_all_fields(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/pulls/7",
        json=_PULL_JSON,
    )
    result = await get_pull_request("owner/repo", 7)
    assert result == {
        "number": 7,
        "title": "Add the thing",
        "url": "https://github.com/o/r/pull/7",
        "state": "closed",
        "merged": True,
        "merged_at": "2026-09-01T10:00:00Z",
        "mergeable": None,
        "mergeable_state": "unknown",
        "head_ref": "feature/thing",
        "base_ref": "main",
        "draft": False,
        "labels": ["spec-change"],
    }


async def test_get_pull_request_returns_none_on_404(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=404)
    result = await get_pull_request("owner/repo", 999)
    assert result is None


async def test_get_pull_request_raises_on_server_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=500)
    with pytest.raises(httpx.HTTPStatusError):
        await get_pull_request("owner/repo", 7)


# ---------------------------------------------------------------------------
# update_issue
# ---------------------------------------------------------------------------

_UPDATE_ISSUE_URL = "https://api.github.com/repos/owner/repo/issues/42"


async def test_update_issue_returns_none_on_404(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="PATCH", url=_UPDATE_ISSUE_URL, status_code=404)
    result = await update_issue("owner/repo", 42, {"state": "closed"})
    assert result is None


async def test_update_issue_returns_number_title_url(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="PATCH",
        url=_UPDATE_ISSUE_URL,
        json={
            "number": 42,
            "title": "Updated title",
            "html_url": "https://github.com/owner/repo/issues/42",
            "state": "closed",
        },
    )
    result = await update_issue("owner/repo", 42, {"title": "Updated title", "state": "closed"})
    assert result == {
        "number": 42,
        "title": "Updated title",
        "url": "https://github.com/owner/repo/issues/42",
    }


async def test_update_issue_raises_on_non_404_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="PATCH",
        url=_UPDATE_ISSUE_URL,
        status_code=422,
        json={"message": "Unprocessable Entity"},
    )
    with pytest.raises(httpx.HTTPStatusError):
        await update_issue("owner/repo", 42, {"state": "invalid"})


# ---------------------------------------------------------------------------
# list_issue_comments / create_issue_comment
# ---------------------------------------------------------------------------

_COMMENTS_URL = "https://api.github.com/repos/owner/repo/issues/42/comments"


def _comment(n: int) -> dict:
    return {
        "user": {"login": f"user{n}"},
        "created_at": f"2026-09-{n % 28 + 1:02d}T10:00:00Z",
        "body": f"comment {n}",
        "html_url": f"https://github.com/owner/repo/issues/42#issuecomment-{n}",
    }


async def test_list_issue_comments_follows_every_page(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{_COMMENTS_URL}?per_page=100&page=1", json=[_comment(n) for n in range(100)]
    )
    httpx_mock.add_response(url=f"{_COMMENTS_URL}?per_page=100&page=2", json=[_comment(100)])

    comments = await list_issue_comments("owner/repo", 42)

    assert [c["body"] for c in comments] == [f"comment {n}" for n in range(101)]
    assert comments[0] == {
        "author": "user0",
        "created_at": "2026-09-01T10:00:00Z",
        "body": "comment 0",
        "url": "https://github.com/owner/repo/issues/42#issuecomment-0",
    }


async def test_list_issue_comments_keeps_comments_from_deleted_accounts(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json=[{**_comment(1), "user": None}])
    comments = await list_issue_comments("owner/repo", 42)
    assert comments[0]["author"] is None


async def test_list_issue_comments_returns_none_on_404(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=404)
    assert await list_issue_comments("owner/repo", 42) is None


async def test_create_issue_comment_returns_url(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_COMMENTS_URL,
        status_code=201,
        json={"html_url": "https://github.com/owner/repo/issues/42#issuecomment-7"},
    )

    url = await create_issue_comment("owner/repo", 42, "Which one?")

    assert url == "https://github.com/owner/repo/issues/42#issuecomment-7"
    assert json.loads(httpx_mock.get_requests()[0].content) == {"body": "Which one?"}


async def test_create_issue_comment_returns_none_on_404(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=_COMMENTS_URL, status_code=404)
    assert await create_issue_comment("owner/repo", 42, "hi") is None


# ---------------------------------------------------------------------------
# get_issue_dependencies
# ---------------------------------------------------------------------------

_BLOCKED_BY_URL = "https://api.github.com/repos/owner/repo/issues/42/dependencies/blocked_by"
_BLOCKING_URL = "https://api.github.com/repos/owner/repo/issues/42/dependencies/blocking"


async def test_get_issue_dependencies_returns_parsed_numbers(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=_BLOCKED_BY_URL,
        json=[{"number": 38, "id": 1001}, {"number": 41, "id": 1002}],
    )
    httpx_mock.add_response(method="GET", url=_BLOCKING_URL, json=[{"number": 45, "id": 1003}])
    result = await get_issue_dependencies("owner/repo", 42)
    assert result == {"blocked_by": [38, 41], "blocking": [45]}


async def test_list_blockers_reports_each_blockers_state_and_close_time(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=_BLOCKED_BY_URL,
        json=[
            {"number": 38, "id": 1001, "state": "closed", "closed_at": "2026-03-01T00:00:00Z"},
            {"number": 41, "id": 1002, "state": "open", "closed_at": None},
        ],
    )

    assert await list_blockers("owner/repo", 42) == [
        {"number": 38, "state": "closed", "closed_at": "2026-03-01T00:00:00Z"},
        {"number": 41, "state": "open", "closed_at": None},
    ]


async def test_list_blockers_returns_none_on_404(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_BLOCKED_BY_URL, status_code=404)

    assert await list_blockers("owner/repo", 42) is None


@pytest.mark.parametrize("which_404", ["blocked_by", "blocking"])
async def test_get_issue_dependencies_returns_none_when_either_endpoint_is_404(
    httpx_mock: HTTPXMock, which_404: str
) -> None:
    blocked_by_code = 404 if which_404 == "blocked_by" else 200
    blocking_code = 404 if which_404 == "blocking" else 200
    httpx_mock.add_response(
        method="GET",
        url=_BLOCKED_BY_URL,
        status_code=blocked_by_code,
        json=[] if blocked_by_code == 200 else {},
    )
    httpx_mock.add_response(
        method="GET",
        url=_BLOCKING_URL,
        status_code=blocking_code,
        json=[] if blocking_code == 200 else {},
    )
    result = await get_issue_dependencies("owner/repo", 42)
    assert result is None


async def test_get_issue_dependencies_raises_on_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="GET", url=_BLOCKED_BY_URL, status_code=500, json={})
    httpx_mock.add_response(method="GET", url=_BLOCKING_URL, json=[])
    with pytest.raises(httpx.HTTPStatusError):
        await get_issue_dependencies("owner/repo", 42)


async def test_get_issue_dependencies_raises_on_error_from_blocking_endpoint(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(method="GET", url=_BLOCKED_BY_URL, json=[])
    httpx_mock.add_response(method="GET", url=_BLOCKING_URL, status_code=500, json={})
    with pytest.raises(httpx.HTTPStatusError):
        await get_issue_dependencies("owner/repo", 42)


async def test_get_issue_dependencies_returns_empty_lists_when_no_deps(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(method="GET", url=_BLOCKED_BY_URL, json=[])
    httpx_mock.add_response(method="GET", url=_BLOCKING_URL, json=[])
    result = await get_issue_dependencies("owner/repo", 42)
    assert result == {"blocked_by": [], "blocking": []}


# ---------------------------------------------------------------------------
# list_blocked_by_numbers
# ---------------------------------------------------------------------------


async def test_list_blocked_by_numbers_returns_parsed_numbers(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=_BLOCKED_BY_URL,
        json=[{"number": 38, "id": 1001}, {"number": 41, "id": 1002}],
    )
    assert await list_blocked_by_numbers("owner/repo", 42) == [38, 41]


async def test_list_blocked_by_numbers_returns_none_on_404(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="GET", url=_BLOCKED_BY_URL, status_code=404, json={})
    assert await list_blocked_by_numbers("owner/repo", 42) is None


async def test_list_blocked_by_numbers_raises_on_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="GET", url=_BLOCKED_BY_URL, status_code=500, json={})
    with pytest.raises(httpx.HTTPStatusError):
        await list_blocked_by_numbers("owner/repo", 42)


# ---------------------------------------------------------------------------
# get_repo_settings
# ---------------------------------------------------------------------------


async def test_get_repo_settings_returns_settings(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json={
            "allow_auto_merge": True,
            "allow_squash_merge": False,
            "allow_merge_commit": True,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
        }
    )
    result = await get_repo_settings("owner/repo")
    assert result == {
        "allow_auto_merge": True,
        "allow_squash_merge": False,
        "allow_merge_commit": True,
        "allow_rebase_merge": False,
        "delete_branch_on_merge": True,
    }


async def test_get_repo_settings_uses_defaults_for_missing_keys(httpx_mock: HTTPXMock) -> None:
    """GitHub API may omit keys for older repos — defaults must be applied correctly."""
    httpx_mock.add_response(json={})  # No merge-related keys
    result = await get_repo_settings("owner/repo")
    assert result["allow_auto_merge"] is False
    assert result["allow_squash_merge"] is True  # important: default is True, not False
    assert result["allow_merge_commit"] is True
    assert result["allow_rebase_merge"] is True
    assert result["delete_branch_on_merge"] is False


async def test_get_repo_settings_raises_on_http_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=403, json={"message": "Forbidden"})
    with pytest.raises(httpx.HTTPStatusError):
        await get_repo_settings("owner/repo")


# ---------------------------------------------------------------------------
# enable_automerge
# ---------------------------------------------------------------------------


async def test_enable_automerge_sends_correct_graphql_payload(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json={
            "data": {
                "enablePullRequestAutoMerge": {
                    "pullRequest": {"autoMergeRequest": {"enabledAt": "2026-01-01"}}
                }
            }
        }
    )
    await enable_automerge("PR_abc123", "SQUASH")
    req = httpx_mock.get_requests()[0]
    body = json.loads(req.content)
    assert body["variables"]["pullRequestId"] == "PR_abc123"
    assert body["variables"]["mergeMethod"] == "SQUASH"
    assert "enablePullRequestAutoMerge" in body["query"]


async def test_enable_automerge_raises_on_http_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=401, json={"message": "Bad credentials"})
    with pytest.raises(httpx.HTTPStatusError):
        await enable_automerge("PR_abc123")


async def test_enable_automerge_raises_on_graphql_errors(httpx_mock: HTTPXMock) -> None:
    """GraphQL errors in a 200 response must raise RuntimeError carrying GitHub's messages."""
    httpx_mock.add_response(json={"errors": [{"message": "Not allowed"}, {"message": "Nope"}]})
    with pytest.raises(RuntimeError, match="GraphQL errors enabling automerge: Not allowed; Nope$"):
        await enable_automerge("PR_abc123")


# ---------------------------------------------------------------------------
# list_lachesis_prs
# ---------------------------------------------------------------------------


async def test_list_lachesis_prs_returns_branch_url_and_number(httpx_mock: HTTPXMock) -> None:
    """Happy path: only lachesis/ branches are returned with correct fields and types."""
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": "lachesis/req-007-auth"},
                "html_url": "https://github.com/owner/repo/pull/7",
                "number": 7,
                "created_at": "2026-09-01T00:00:00Z",
            },
            {
                "head": {"ref": "feature/not-lachesis"},
                "html_url": "https://github.com/owner/repo/pull/8",
                "number": 8,
                "created_at": "2026-09-02T00:00:00Z",
            },
        ]
    )
    result = await list_lachesis_prs("owner/repo")
    assert len(result) == 1
    assert result[0]["branch"] == "lachesis/req-007-auth"
    assert result[0]["pr_url"] == "https://github.com/owner/repo/pull/7"
    assert result[0]["pr_number"] == 7
    assert isinstance(result[0]["pr_number"], int)
    assert result[0]["created_at"] == "2026-09-01T00:00:00Z"


@pytest.mark.parametrize(
    ("auto_merge", "enabled"), [(None, False), ({"merge_method": "squash"}, True)]
)
async def test_list_lachesis_prs_says_whether_automerge_is_enabled(
    httpx_mock: HTTPXMock, auto_merge: dict | None, enabled: bool
) -> None:
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": "lachesis/req-007-auth"},
                "html_url": "https://github.com/owner/repo/pull/7",
                "number": 7,
                "auto_merge": auto_merge,
            }
        ]
    )
    [pr] = await list_lachesis_prs("owner/repo")
    assert pr["automerge_enabled"] is enabled


async def test_list_lachesis_prs_reports_no_created_at_without_dropping_the_pr(
    httpx_mock: HTTPXMock,
) -> None:
    """A missing timestamp costs the dashboard a sort key, not the whole PR list."""
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": "lachesis/req-007-auth"},
                "html_url": "https://github.com/owner/repo/pull/7",
                "number": 7,
            }
        ]
    )
    result = await list_lachesis_prs("owner/repo")
    assert len(result) == 1
    assert result[0]["pr_number"] == 7
    assert result[0]["created_at"] is None


async def test_list_lachesis_prs_returns_empty_on_missing_number_key(httpx_mock: HTTPXMock) -> None:
    """Malformed API response missing 'number' triggers silent error path returning empty list."""
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": "lachesis/req-007-auth"},
                "html_url": "https://github.com/owner/repo/pull/7",
                # "number" intentionally absent — triggers KeyError silent path
            }
        ]
    )
    result = await list_lachesis_prs("owner/repo")
    assert result == []


async def test_list_lachesis_prs_returns_empty_when_no_lachesis_prs(httpx_mock: HTTPXMock) -> None:
    """When there are no open PRs at all, result is an empty list."""
    httpx_mock.add_response(json=[])
    result = await list_lachesis_prs("owner/repo")
    assert result == []


async def test_list_lachesis_prs_raises_on_http_error(httpx_mock: HTTPXMock) -> None:
    """A fetch that could not run is not the same fact as a repo with no Lachesis PRs."""
    httpx_mock.add_response(status_code=403, json={"message": "Forbidden"})
    with pytest.raises(httpx.HTTPStatusError):
        await list_lachesis_prs("owner/repo")


async def test_list_labelled_prs_raises_on_http_error(httpx_mock: HTTPXMock) -> None:
    """A failed lookup must not read as "no PRs carry this label"."""
    httpx_mock.add_response(status_code=500)
    with pytest.raises(httpx.HTTPStatusError):
        await list_labelled_prs("owner/repo", "spec-change")


async def test_list_pull_request_files_raises_on_http_error(httpx_mock: HTTPXMock) -> None:
    """A failed lookup must not read as "this PR changes no files"."""
    httpx_mock.add_response(status_code=500)
    with pytest.raises(httpx.HTTPStatusError):
        await list_pull_request_files("owner/repo", 7)


async def test_get_pull_request_files_keeps_each_patch_and_says_when_github_withholds_it(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        json=[
            {"filename": "a.md", "status": "modified", "patch": "@@ -1 +1 @@\n+x"},
            {"filename": "b.md", "status": "renamed", "previous_filename": "c.md"},
        ]
    )

    files = await get_pull_request_files("owner/repo", 7)

    assert files == [
        {
            "filename": "a.md",
            "previous_filename": None,
            "status": "modified",
            "patch": "@@ -1 +1 @@\n+x",
        },
        {"filename": "b.md", "previous_filename": "c.md", "status": "renamed", "patch": None},
    ]


# ---------------------------------------------------------------------------
# get_scenario_results
# ---------------------------------------------------------------------------


def _check_run(text: str, *, completed_at: str, sha: str, status: str = "completed") -> dict:
    return {
        "name": "scenario-results",
        "head_sha": sha,
        "status": status,
        "completed_at": completed_at,
        "output": {"text": text},
    }


async def test_get_scenario_results_reads_the_latest_completed_run(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=(
            "https://api.github.com/repos/owner/repo/commits/main/check-runs"
            "?check_name=scenario-results&per_page=100"
        ),
        json={
            "check_runs": [
                _check_run('{"a": {"S": "failed"}}', completed_at="2026-10-01T00:00:00Z", sha="1"),
                _check_run('{"a": {"S": "passed"}}', completed_at="2026-10-02T00:00:00Z", sha="2"),
                _check_run("", completed_at="", sha="3", status="in_progress"),
            ]
        },
    )

    assert await get_scenario_results("owner/repo") == {
        "commit": "2",
        "capabilities": {"a": {"S": "passed"}},
    }


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (404, {"message": "Not Found"}),
        (422, {"message": "No commit found for SHA: main"}),
        (200, {"check_runs": []}),
        (200, {"check_runs": [_check_run("not json", completed_at="x", sha="1")]}),
        (200, {"check_runs": [_check_run('{"a": ["S"]}', completed_at="x", sha="1")]}),
    ],
)
async def test_get_scenario_results_is_none_when_nothing_readable_is_published(
    httpx_mock: HTTPXMock, status: int, body: dict
) -> None:
    httpx_mock.add_response(status_code=status, json=body)

    assert await get_scenario_results("owner/repo") is None


async def test_get_scenario_results_raises_on_http_error(httpx_mock: HTTPXMock) -> None:
    """A failed read must not read as "nothing is published"."""
    httpx_mock.add_response(status_code=500)
    with pytest.raises(httpx.HTTPStatusError):
        await get_scenario_results("owner/repo")


# ---------------------------------------------------------------------------
# get_merge_base
# ---------------------------------------------------------------------------

_PULLS_7 = "https://api.github.com/repos/owner/repo/pulls/7"


async def test_get_merge_base_returns_the_merge_base_and_head(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_PULLS_7, json={"base": {"sha": "b1"}, "head": {"sha": "h1"}})
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/compare/b1...h1",
        json={"merge_base_commit": {"sha": "mb"}},
    )

    assert await get_merge_base("owner/repo", 7) == ("mb", "h1")


async def test_get_merge_base_of_a_missing_pull_request(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_PULLS_7, status_code=404)

    assert await get_merge_base("owner/repo", 7) is None


async def test_get_merge_base_raises_on_http_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_PULLS_7, json={"base": {"sha": "b1"}, "head": {"sha": "h1"}})
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/compare/b1...h1", status_code=500
    )

    with pytest.raises(httpx.HTTPStatusError):
        await get_merge_base("owner/repo", 7)


# ---------------------------------------------------------------------------
# list_open_issues
# ---------------------------------------------------------------------------


async def test_list_open_issues_sorted_ascending(httpx_mock: HTTPXMock) -> None:
    """Open issues come back ascending by number regardless of API order."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues?state=open&per_page=100",
        json=[
            {
                "number": 7,
                "title": "Later",
                "body": None,
                "state": "open",
                "created_at": "2026-02-01T00:00:00Z",
            },
            {
                "number": 3,
                "title": "Earlier",
                "body": "b",
                "state": "open",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ],
    )

    result = await list_open_issues("owner/repo")

    assert result == [
        {
            "number": 3,
            "title": "Earlier",
            "labels": [],
            "body": "b",
            "created_at": "2026-01-01T00:00:00Z",
        },
        {
            "number": 7,
            "title": "Later",
            "labels": [],
            "body": "",
            "created_at": "2026-02-01T00:00:00Z",
        },
    ]


async def test_list_open_issues_excludes_pull_requests(httpx_mock: HTTPXMock) -> None:
    """Entries carrying a pull_request key are not issues."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues?state=open&per_page=100",
        json=[
            {
                "number": 1,
                "title": "An issue",
                "state": "open",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {"number": 2, "title": "A PR", "state": "open", "pull_request": {"url": "..."}},
        ],
    )

    result = await list_open_issues("owner/repo")

    assert [issue["number"] for issue in result] == [1]


async def test_list_open_issues_excludes_closed_issues(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues?state=open&per_page=100",
        json=[
            {"number": 1, "title": "Open", "state": "open", "created_at": "2026-01-01T00:00:00Z"},
            {"number": 2, "title": "Done", "state": "closed", "created_at": "2026-01-01T00:00:00Z"},
        ],
    )

    result = await list_open_issues("owner/repo")

    assert [issue["number"] for issue in result] == [1]


async def test_list_open_issues_raises_on_error(httpx_mock: HTTPXMock) -> None:
    """A failing response propagates as an HTTPStatusError."""
    httpx_mock.add_response(status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        await list_open_issues("owner/repo")


# ---------------------------------------------------------------------------
# get_issue_activity
# ---------------------------------------------------------------------------


def _activity_node() -> dict:
    """A closed issue with one merged PR and one open PR carrying a failed check."""
    return {
        "number": 42,
        "state": "CLOSED",
        "updatedAt": "2026-08-31T18:35:09Z",
        "labels": {"nodes": [{"name": "archon:in-progress"}]},
        "timelineItems": {"nodes": [{"closer": {"__typename": "Commit"}}]},
        "closedByPullRequestsReferences": {
            "nodes": [
                {
                    "number": 60,
                    "state": "MERGED",
                    "merged": True,
                    "updatedAt": "2026-08-30T10:00:00Z",
                    "isDraft": False,
                    "commits": {"nodes": []},
                },
                {
                    "number": 61,
                    "state": "OPEN",
                    "merged": False,
                    "updatedAt": "2026-09-01T10:00:00Z",
                    "isDraft": True,
                    "commits": {
                        "nodes": [
                            {
                                "commit": {
                                    "statusCheckRollup": {
                                        "contexts": {
                                            "nodes": [
                                                {
                                                    "__typename": "CheckRun",
                                                    "conclusion": "FAILURE",
                                                    "completedAt": "2026-08-30T22:30:46Z",
                                                },
                                                {
                                                    "__typename": "StatusContext",
                                                    "state": "SUCCESS",
                                                    "createdAt": "2026-08-30T22:00:00Z",
                                                },
                                            ]
                                        }
                                    }
                                }
                            }
                        ]
                    },
                },
            ]
        },
    }


async def test_get_issue_activity_makes_no_request_without_issues(httpx_mock: HTTPXMock) -> None:
    """A repo whose requirements link nothing costs no GraphQL call."""
    assert await get_issue_activity("owner/repo", []) == {"facts": {}, "failure": None}
    assert httpx_mock.get_requests() == []


async def test_get_issue_activity_normalizes_issues_and_their_pull_requests(
    httpx_mock: HTTPXMock,
) -> None:
    """The GraphQL shape is flattened into the facts derivation consumes."""
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"i42": _activity_node()}}}
    )

    facts = (await get_issue_activity("owner/repo", [42]))["facts"]

    assert facts[42]["state"] == "CLOSED"
    assert facts[42]["updated_at"] == "2026-08-31T18:35:09Z"
    assert facts[42]["labels"] == ["archon:in-progress"]
    assert facts[42]["closed_by_commit"] is True
    assert [p["number"] for p in facts[42]["pulls"]] == [60, 61]
    assert facts[42]["pulls"][0]["merged"] is True
    assert facts[42]["pulls"][1]["is_draft"] is True
    assert facts[42]["pulls"][1]["checks"] == [
        {"conclusion": "FAILURE", "at": "2026-08-30T22:30:46Z"},
        {"conclusion": "SUCCESS", "at": "2026-08-30T22:00:00Z"},
    ]


async def test_get_issue_activity_drops_an_issue_that_no_longer_exists(
    httpx_mock: HTTPXMock,
) -> None:
    """A deleted issue must not darken its whole repo.

    GitHub answers 200 with a null node *and* a NOT_FOUND naming that alias, so
    reading the errors without stripping those marks every sibling unavailable.
    """
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={
            "data": {"repository": {"i42": _activity_node(), "i99": None}},
            "errors": [{"type": "NOT_FOUND", "path": ["repository", "i99"]}],
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={
            "data": {"repository": {"p99": None}},
            "errors": [{"type": "NOT_FOUND", "path": ["repository", "p99"]}],
        },
    )

    activity = await get_issue_activity("owner/repo", [42, 99])

    assert list(activity["facts"]) == [42]
    assert activity["failure"] is None


def _closed_by_hand(number: int = 42) -> dict:
    """A closed issue nothing links a merged pull request or closing commit to."""
    return {
        "number": number,
        "state": "CLOSED",
        "updatedAt": "2026-08-31T18:35:09Z",
        "labels": {"nodes": []},
        "timelineItems": {"nodes": [{"closer": None}]},
        "closedByPullRequestsReferences": {"nodes": []},
    }


def _pull_node(number: int, *, merged: bool = True) -> dict:
    return {
        "number": number,
        "state": "MERGED" if merged else "CLOSED",
        "merged": merged,
        "updatedAt": "2026-08-30T10:00:00Z",
        "mergedAt": "2026-08-30T10:00:00Z" if merged else None,
        "mergeCommit": {"oid": f"sha{number}"} if merged else None,
        "isDraft": False,
        "commits": {"nodes": []},
    }


def _comments_node(number: int, *comments: tuple[str, str]) -> dict:
    return {
        "number": number,
        "comments": {
            "nodes": [
                {"authorAssociation": association, "body": body} for association, body in comments
            ]
        },
    }


async def test_get_issue_activity_counts_a_merged_pull_request_named_by_the_owner(
    httpx_mock: HTTPXMock,
) -> None:
    """An issue closed by hand lands when someone with write access names its merged PR."""
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"i42": _closed_by_hand()}}}
    )
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={
            "data": {
                "repository": {
                    "c42": _comments_node(
                        42,
                        ("OWNER", "Delivered by merged PR #384, see #42 and #998"),
                        ("NONE", "Fixed in #500"),
                    )
                }
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={
            "data": {"repository": {"p384": _pull_node(384), "p998": None}},
            "errors": [{"type": "NOT_FOUND", "path": ["repository", "p998"]}],
        },
    )

    activity = await get_issue_activity("owner/repo", [42])

    pulls = activity["facts"][42]["pulls"]
    assert [(pull["number"], pull["merged"]) for pull in pulls] == [(384, True)]
    assert pulls[0]["merge_commit"] == "sha384"
    assert activity["failure"] is None
    lookup = json.loads(httpx_mock.get_requests()[2].content)["query"]
    assert "p384:" in lookup and "p998:" in lookup
    assert "p500:" not in lookup and "p42:" not in lookup


async def test_get_issue_activity_ignores_a_named_pull_request_that_did_not_merge(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"i42": _closed_by_hand()}}}
    )
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"repository": {"c42": _comments_node(42, ("OWNER", "**PR**: #1008"))}}},
    )
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"repository": {"p1008": _pull_node(1008, merged=False)}}},
    )

    activity = await get_issue_activity("owner/repo", [42])

    assert activity["facts"][42]["pulls"] == []


async def test_get_issue_activity_skips_comments_when_every_closed_issue_landed(
    httpx_mock: HTTPXMock,
) -> None:
    """The common case costs one request, however many issues are closed."""
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"i42": _activity_node()}}}
    )

    await get_issue_activity("owner/repo", [42])

    assert len(httpx_mock.get_requests()) == 1


async def test_get_issue_activity_reads_a_pull_request_linked_in_place_of_an_issue(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={
            "data": {"repository": {"i21": None}},
            "errors": [{"type": "NOT_FOUND", "path": ["repository", "i21"]}],
        },
    )
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"p21": _pull_node(21)}}}
    )

    activity = await get_issue_activity("owner/repo", [21])

    issue = activity["facts"][21]
    assert issue["state"] == "CLOSED"
    assert [(pull["number"], pull["merged"]) for pull in issue["pulls"]] == [(21, True)]
    assert activity["failure"] is None


@pytest.mark.parametrize(
    ("pull_state", "issue_state", "closed_without_merge"),
    [("OPEN", "OPEN", False), ("CLOSED", "CLOSED", True)],
)
async def test_get_issue_activity_reads_an_unmerged_pull_request_linked_in_place_of_an_issue(
    httpx_mock: HTTPXMock, pull_state: str, issue_state: str, closed_without_merge: bool
) -> None:
    """An open pull request is work in flight; only a closed unmerged one is a false close."""
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={
            "data": {"repository": {"i21": None}},
            "errors": [{"type": "NOT_FOUND", "path": ["repository", "i21"]}],
        },
    )
    pull = {**_pull_node(21, merged=False), "state": pull_state}
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"p21": pull}}}
    )

    facts = (await get_issue_activity("owner/repo", [21]))["facts"]

    assert facts[21]["state"] == issue_state
    derived = derive(None, [21], facts, on_pr_branch=False)
    assert derived["status"] == "in-progress"
    assert derived["signals"]["closed_without_merge"] is closed_without_merge


async def test_get_issue_activity_leaves_out_an_issue_whose_comments_could_not_be_read(
    httpx_mock: HTTPXMock,
) -> None:
    """Unread landing evidence must not read as none: that is a false closed-without-merge."""
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"i42": _closed_by_hand()}}}
    )
    httpx_mock.add_response(method="POST", url=_GRAPHQL_URL, status_code=500, json={})

    activity = await get_issue_activity("owner/repo", [42])

    assert activity == {"facts": {}, "failure": "transient"}
    derived = derive(None, [42], activity["facts"], on_pr_branch=False, failure="transient")
    assert derived["unavailable_cause"] == "transient"
    assert derived["signals"]["closed_without_merge"] is False


async def test_get_issue_activity_leaves_out_an_issue_whose_named_pull_could_not_be_read(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"i42": _closed_by_hand()}}}
    )
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"repository": {"c42": _comments_node(42, ("OWNER", "Done in #384"))}}},
    )
    httpx_mock.add_response(method="POST", url=_GRAPHQL_URL, status_code=500, json={})

    activity = await get_issue_activity("owner/repo", [42])

    assert activity == {"facts": {}, "failure": "transient"}


async def test_get_issue_activity_keeps_an_issue_a_readable_named_pull_landed(
    httpx_mock: HTTPXMock,
) -> None:
    """One unreadable mention does not unsettle an issue another mention already landed."""
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"i42": _closed_by_hand()}}}
    )
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"repository": {"c42": _comments_node(42, ("OWNER", "#384, also #999"))}}},
    )
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={
            "data": {"repository": {"p384": _pull_node(384), "p999": None}},
            "errors": [{"type": "INTERNAL"}],
        },
    )

    activity = await get_issue_activity("owner/repo", [42])

    assert [pull["number"] for pull in activity["facts"][42]["pulls"]] == [384]
    assert activity["failure"] == "transient"


async def test_get_issue_activity_leaves_out_an_unlanded_issue_once_rate_limited(
    httpx_mock: HTTPXMock,
) -> None:
    """A batch that read before the quota ran out cannot have its comments checked."""
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={
            "data": {
                "repository": {"i1": _closed_by_hand(1), "i2": {**_activity_node(), "number": 2}}
            }
        },
    )
    httpx_mock.add_response(method="POST", url=_GRAPHQL_URL, status_code=429, json={})

    activity = await get_issue_activity("owner/repo", [1, 2, *range(3, 27)])

    assert list(activity["facts"]) == [2]
    assert activity["failure"] == "rate_limited"
    assert len(httpx_mock.get_requests()) == 2


async def test_get_issue_activity_does_not_reask_numbers_of_a_failed_chunk(
    httpx_mock: HTTPXMock,
) -> None:
    """A null node from a batch that failed says nothing about whether the issue exists."""
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"repository": {"i42": None}}, "errors": [{"type": "INTERNAL"}]},
    )

    activity = await get_issue_activity("owner/repo", [42])

    assert len(httpx_mock.get_requests()) == 1
    assert activity == {"facts": {}, "failure": "transient"}


async def test_get_issue_activity_batches_large_issue_sets(httpx_mock: HTTPXMock) -> None:
    """More issues than fit one query are fetched in chunks, not one call each."""
    for _ in range(2):
        httpx_mock.add_response(method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {}}})

    await get_issue_activity("owner/repo", list(range(1, 27)))

    assert len(httpx_mock.get_requests()) == 2


async def test_get_issue_activity_names_a_transient_cause_on_http_error(
    httpx_mock: HTTPXMock,
) -> None:
    """An unreadable GitHub degrades the caller rather than failing it, and says why."""
    httpx_mock.add_response(method="POST", url=_GRAPHQL_URL, status_code=500, json={})

    assert await get_issue_activity("owner/repo", [42]) == {
        "facts": {},
        "failure": "transient",
    }


async def test_get_issue_activity_names_an_inaccessible_repo(httpx_mock: HTTPXMock) -> None:
    """A repo the token cannot see errors at the repository path, not at an alias."""
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={
            "data": {"repository": None},
            "errors": [{"type": "NOT_FOUND", "path": ["repository"]}],
        },
    )

    assert await get_issue_activity("owner/repo", [42]) == {
        "facts": {},
        "failure": "inaccessible",
    }


async def test_get_issue_activity_names_a_graphql_rate_limit(httpx_mock: HTTPXMock) -> None:
    """Spent quota is reported apart from a refusal, since only one improves by waiting."""
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"errors": [{"type": "RATE_LIMITED"}]}
    )

    assert (await get_issue_activity("owner/repo", [42]))["failure"] == "rate_limited"


async def test_get_issue_activity_reads_the_rate_limit_off_a_403(httpx_mock: HTTPXMock) -> None:
    """A 403 with no quota left is a rate limit; a plain 404 is not."""
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        status_code=403,
        headers={"x-ratelimit-remaining": "0"},
        json={},
    )
    assert (await get_issue_activity("owner/repo", [42]))["failure"] == "rate_limited"

    httpx_mock.add_response(method="POST", url=_GRAPHQL_URL, status_code=404, json={})
    assert (await get_issue_activity("owner/repo", [42]))["failure"] == "inaccessible"


async def test_get_issue_activity_keeps_the_chunks_that_worked(httpx_mock: HTTPXMock) -> None:
    """One failed batch costs its own issues, not the repo's."""
    httpx_mock.add_response(method="POST", url=_GRAPHQL_URL, status_code=500, json={})
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"i42": _activity_node()}}}
    )

    activity = await get_issue_activity("owner/repo", [*range(1, 26), 42])

    assert list(activity["facts"]) == [42]
    assert activity["failure"] == "transient"


async def test_get_issue_activity_stops_asking_once_rate_limited(httpx_mock: HTTPXMock) -> None:
    """A cause that cannot improve on a retry must not spend the remaining quota."""
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, status_code=429, json={}, is_reusable=True
    )

    activity = await get_issue_activity("owner/repo", [*range(1, 26), 42])

    assert activity["failure"] == "rate_limited"
    assert len(httpx_mock.get_requests()) == 1


async def test_get_issue_activity_normalizes_landing_and_blocker_facts(
    httpx_mock: HTTPXMock,
) -> None:
    """Merge times, close times and open blockers come back flattened with their repo."""
    node = _activity_node()
    node["closedAt"] = "2026-08-31T18:35:09Z"
    node["blockedBy"] = {
        "nodes": [
            {
                "number": 99,
                "state": "OPEN",
                "updatedAt": "2026-09-01T00:00:00Z",
                "repository": {"nameWithOwner": "owner/other"},
            },
            {
                "number": 98,
                "state": "CLOSED",
                "updatedAt": "2026-07-01T00:00:00Z",
                "repository": {"nameWithOwner": "owner/repo"},
            },
        ]
    }
    node["closedByPullRequestsReferences"]["nodes"][0]["mergedAt"] = "2026-08-30T09:59:00Z"
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"i42": node}}}
    )

    facts = (await get_issue_activity("owner/repo", [42]))["facts"]

    assert facts[42]["closed_at"] == "2026-08-31T18:35:09Z"
    assert facts[42]["pulls"][0]["merged_at"] == "2026-08-30T09:59:00Z"
    assert facts[42]["blocked_by"] == [
        {"repo": "owner/other", "number": 99, "last_activity": "2026-09-01T00:00:00Z"}
    ]


async def test_get_issue_activity_normalizes_a_response_without_the_newer_fields(
    httpx_mock: HTTPXMock,
) -> None:
    """A response missing the landing and blocker fields still yields usable facts."""
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"i42": _activity_node()}}}
    )

    facts = (await get_issue_activity("owner/repo", [42]))["facts"]

    assert facts[42]["closed_at"] is None
    assert facts[42]["blocked_by"] == []
    assert all(pull["merged_at"] is None for pull in facts[42]["pulls"])


# ---------------------------------------------------------------------------
# merge commits and production deployments
# ---------------------------------------------------------------------------


async def test_get_issue_activity_surfaces_the_merge_commit(httpx_mock: HTTPXMock) -> None:
    node = _activity_node()
    node["closedByPullRequestsReferences"]["nodes"][0]["mergeCommit"] = {"oid": "deadbeef"}
    httpx_mock.add_response(
        method="POST", url=_GRAPHQL_URL, json={"data": {"repository": {"i42": node}}}
    )

    facts = (await get_issue_activity("owner/repo", [42]))["facts"]

    assert facts[42]["pulls"][0]["merge_commit"] == "deadbeef"
    assert facts[42]["pulls"][1]["merge_commit"] is None


_DEPLOYMENTS_URL = "https://api.github.com/repos/owner/repo/deployments?sha=abc123&per_page=100"


def _deployment(deployment_id: int = 1, *, production: bool = True, task: str = "deploy") -> dict:
    return {
        "id": deployment_id,
        "sha": "abc123",
        "task": task,
        "environment": "production" if production else "preview",
        "production_environment": production,
    }


def _statuses_url(deployment_id: int) -> str:
    return (
        f"https://api.github.com/repos/owner/repo/deployments/{deployment_id}/statuses?per_page=10"
    )


async def test_get_production_deployment_reports_success(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_DEPLOYMENTS_URL, json=[_deployment()])
    httpx_mock.add_response(
        url=_statuses_url(1), json=[{"state": "success", "log_url": "https://example/log"}]
    )

    assert await get_production_deployment("owner/repo", "abc123") == {
        "state": "success",
        "url": "https://example/log",
    }


async def test_get_production_deployment_reports_failure_with_its_url(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=_DEPLOYMENTS_URL, json=[_deployment()])
    httpx_mock.add_response(
        url=_statuses_url(1), json=[{"state": "failure", "target_url": "https://example/failed"}]
    )

    assert await get_production_deployment("owner/repo", "abc123") == {
        "state": "failure",
        "url": "https://example/failed",
    }


async def test_get_production_deployment_ignores_a_preview_environment(
    httpx_mock: HTTPXMock,
) -> None:
    """A PR preview deployment for the same sha is not production."""
    httpx_mock.add_response(url=_DEPLOYMENTS_URL, json=[_deployment(production=False)])

    assert await get_production_deployment("owner/repo", "abc123") is None


async def test_get_production_deployment_ignores_a_non_deploy_task(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_DEPLOYMENTS_URL, json=[_deployment(task="deploy:migrations")])

    assert await get_production_deployment("owner/repo", "abc123") is None


async def test_get_production_deployment_lets_success_win_over_a_sibling_failure(
    httpx_mock: HTTPXMock,
) -> None:
    """A retried deploy must not burn the requirement's single notification."""
    httpx_mock.add_response(url=_DEPLOYMENTS_URL, json=[_deployment(1), _deployment(2)])
    httpx_mock.add_response(url=_statuses_url(1), json=[{"state": "failure", "log_url": "u"}])
    httpx_mock.add_response(url=_statuses_url(2), json=[{"state": "success", "log_url": "v"}])

    assert await get_production_deployment("owner/repo", "abc123") == {
        "state": "success",
        "url": "v",
    }


async def test_get_production_deployment_reports_pending_while_in_progress(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=_DEPLOYMENTS_URL, json=[_deployment()])
    httpx_mock.add_response(url=_statuses_url(1), json=[{"state": "in_progress"}])

    assert await get_production_deployment("owner/repo", "abc123") == {
        "state": "pending",
        "url": None,
    }


async def test_get_production_deployment_looks_past_a_superseding_inactive_status(
    httpx_mock: HTTPXMock,
) -> None:
    """GitHub marks a deployment inactive when a later one supersedes it, not when it failed."""
    httpx_mock.add_response(url=_DEPLOYMENTS_URL, json=[_deployment()])
    httpx_mock.add_response(
        url=_statuses_url(1),
        json=[{"state": "inactive"}, {"state": "success", "log_url": "https://example/log"}],
    )

    assert await get_production_deployment("owner/repo", "abc123") == {
        "state": "success",
        "url": "https://example/log",
    }


async def test_get_production_deployment_looks_past_a_page_of_preview_deployments(
    httpx_mock: HTTPXMock,
) -> None:
    """Previews for the same sha must not crowd production off the fetched page."""
    listing = [*(_deployment(i, production=False) for i in range(1, 9)), _deployment(9)]

    def page(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=listing[: int(request.url.params["per_page"])])

    httpx_mock.add_callback(page, url=re.compile(r"^https://api\.github\.com/\S+/deployments\?"))
    httpx_mock.add_response(url=_statuses_url(9), json=[{"state": "success", "log_url": "v"}])

    assert await get_production_deployment("owner/repo", "abc123") == {
        "state": "success",
        "url": "v",
    }


async def test_get_production_deployment_stops_after_five_production_deployments(
    httpx_mock: HTTPXMock,
) -> None:
    """Only the newest five matching deployments are worth the status requests."""
    httpx_mock.add_response(url=_DEPLOYMENTS_URL, json=[_deployment(i) for i in range(1, 7)])
    for deployment_id in range(1, 6):
        httpx_mock.add_response(
            url=_statuses_url(deployment_id), json=[{"state": "failure", "log_url": "u"}]
        )

    assert await get_production_deployment("owner/repo", "abc123") == {
        "state": "failure",
        "url": "u",
    }


async def test_get_production_deployment_withholds_failure_when_a_sibling_is_unreadable(
    httpx_mock: HTTPXMock,
) -> None:
    """The unread sibling could be the successful retry, so no claim is made."""
    httpx_mock.add_response(url=_DEPLOYMENTS_URL, json=[_deployment(1), _deployment(2)])
    httpx_mock.add_response(url=_statuses_url(1), json=[{"state": "failure", "log_url": "u"}])
    httpx_mock.add_response(url=_statuses_url(2), status_code=502)

    assert await get_production_deployment("owner/repo", "abc123") is None


async def test_get_production_deployment_reports_success_past_an_unreadable_sibling(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=_statuses_url(1), status_code=502)
    httpx_mock.add_response(url=_DEPLOYMENTS_URL, json=[_deployment(1), _deployment(2)])
    httpx_mock.add_response(url=_statuses_url(2), json=[{"state": "success", "log_url": "v"}])

    assert await get_production_deployment("owner/repo", "abc123") == {
        "state": "success",
        "url": "v",
    }


async def test_get_production_deployment_returns_none_on_error(httpx_mock: HTTPXMock) -> None:
    """Degrades rather than raising: a caller deciding on a claim must make none."""
    httpx_mock.add_response(url=_DEPLOYMENTS_URL, status_code=500)

    assert await get_production_deployment("owner/repo", "abc123") is None


# ---------------------------------------------------------------------------
# failure_from_exception
# ---------------------------------------------------------------------------


def _status_error(status_code: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.github.com/repos/owner/repo/contents/requirements")
    response = httpx.Response(status_code, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("failed", request=request, response=response)


def test_failure_from_exception_reads_spent_quota_as_rate_limited() -> None:
    error = _status_error(403, {"x-ratelimit-remaining": "0"})

    assert failure_from_exception(error) == "rate_limited"


def test_failure_from_exception_reads_a_404_as_inaccessible() -> None:
    assert failure_from_exception(_status_error(404)) == "inaccessible"


def test_failure_from_exception_reads_a_server_error_as_transient() -> None:
    assert failure_from_exception(_status_error(500)) == "transient"


def test_failure_from_exception_calls_an_unrecognised_exception_transient() -> None:
    """A cause we cannot name must not read as a permanent verdict about the repo."""
    assert failure_from_exception(FileNotFoundError("gone")) == "transient"


# ---------------------------------------------------------------------------
# create_repo
# ---------------------------------------------------------------------------


async def test_create_repo_raises_on_other_refusals(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.github.com/user/repos",
        status_code=422,
        json={
            "message": "Repository creation failed.",
            "errors": [
                {
                    "resource": "Repository",
                    "code": "custom",
                    "field": "name",
                    "message": "name is too long (maximum is 100 characters)",
                }
            ],
        },
    )
    with pytest.raises(httpx.HTTPStatusError):
        await create_repo("x" * 101, description="d", private=True)
