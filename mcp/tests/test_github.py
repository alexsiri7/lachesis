"""Unit tests for mcp/github.py using pytest-httpx to mock httpx."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from github import (
    _headers,
    add_issue_dependency,
    add_to_project,
    create_branch,
    create_file,
    create_issue,
    create_pull_request,
    enable_automerge,
    find_pr_branch,
    get_branch_sha,
    get_file,
    get_issue,
    get_issue_dependencies,
    get_repo_settings,
    list_files,
    list_lachesis_prs,
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
# find_pr_branch
# ---------------------------------------------------------------------------


async def test_find_pr_branch_returns_matching_ref(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json=[
            {"head": {"ref": "lachesis/req-002-other"}},
            {"head": {"ref": "lachesis/req-001-user-auth"}},
        ]
    )
    result = await find_pr_branch("owner/repo", "lachesis/req-001-")
    assert result == "lachesis/req-001-user-auth"


async def test_find_pr_branch_returns_none_when_no_match(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=[{"head": {"ref": "lachesis/req-002-other"}}])
    result = await find_pr_branch("owner/repo", "lachesis/req-001-")
    assert result is None


async def test_find_pr_branch_returns_none_when_empty(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=[])
    result = await find_pr_branch("owner/repo", "lachesis/req-001-")
    assert result is None


async def test_find_pr_branch_raises_on_http_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=403, json={"message": "Forbidden"})
    with pytest.raises(httpx.HTTPStatusError):
        await find_pr_branch("owner/repo", "lachesis/req-001-")


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
        json={
            "number": 7,
            "html_url": "https://github.com/owner/repo/pull/7",
            "node_id": "PR_abc123",
        },
    )
    result = await create_pull_request(
        "owner/repo", "title", "body text", "lachesis/req-001-feat", "main"
    )
    assert result == {
        "number": 7,
        "url": "https://github.com/owner/repo/pull/7",
        "node_id": "PR_abc123",
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
    """GraphQL errors in a 200 response must raise RuntimeError."""
    httpx_mock.add_response(json={"errors": [{"message": "Not allowed"}]})
    with pytest.raises(RuntimeError, match="GraphQL errors enabling automerge"):
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
            },
            {
                "head": {"ref": "feature/not-lachesis"},
                "html_url": "https://github.com/owner/repo/pull/8",
                "number": 8,
            },
        ]
    )
    result = await list_lachesis_prs("owner/repo")
    assert len(result) == 1
    assert result[0]["branch"] == "lachesis/req-007-auth"
    assert result[0]["pr_url"] == "https://github.com/owner/repo/pull/7"
    assert result[0]["pr_number"] == 7
    assert isinstance(result[0]["pr_number"], int)


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
