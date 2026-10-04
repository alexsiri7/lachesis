"""Unit tests for mcp/tools/requirements.py — link_issues."""

from __future__ import annotations

import base64
import contextlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import frontmatter
import httpx
import pytest
from pytest_httpx import HTTPXMock

from auth import current_db_user_id
from tools.requirements import (
    _MAX_WRITE_ATTEMPTS,
    add_dependency,
    create_github_issues,
    create_requirement,
    delete_requirement,
    get_backlog,
    get_comments,
    get_dashboard,
    get_dependencies,
    get_github_issue,
    get_initial_instructions,
    get_pull_request,
    get_requirement,
    link_issues,
    list_requirements,
    move_issue,
    next_issue,
    post_comment,
    set_backlog_flags,
    update_github_issue,
    update_requirement,
)

_NAME = "001-user-auth.md"
_PATH = "requirements/001-user-auth.md"
_LIST_RESP = [{"name": _NAME, "path": _PATH}]


def _make_file_response(content: str, sha: str) -> dict:
    """Build a GitHub Contents API file-object response."""
    encoded = base64.b64encode(content.encode()).decode("ascii")
    return {
        "name": _NAME,
        "path": _PATH,
        "content": encoded,
        "sha": sha,
        "type": "file",
    }


_SAMPLE_MD = """\
---
id: "001"
title: "User authentication"
status: "planned"
github_issue: null
created: "2026-05-11"
updated: "2026-05-11"
---

## Why

Users need to save their rankings.

## What

Implement JWT auth.

## Issues

"""

_ISSUES = [
    {"number": 42, "title": "Set up JWT middleware"},
    {"number": 43, "title": "Build login endpoints"},
]

_LINKED_MD = """\
---
id: "001"
title: "User authentication"
status: "planned"
github_issue: 42
created: "2026-05-11"
updated: "2026-05-11"
---

## Why

Users need to save their rankings.
"""


_BULLETED_MD = """\
---
id: "001"
title: "User authentication"
status: "draft"
github_issue: null
created: "2026-05-11"
updated: "2026-05-11"
---

## Why

Users need to save their rankings.

## Issues

- #42 — Set up JWT middleware
- #43 — Build login endpoints
"""

_GRAPHQL_URL = "https://api.github.com/graphql"

_UNLINKED_DERIVED = {
    "status": None,
    "source": "unlinked",
    "unavailable_cause": None,
    "diverged": False,
    "last_activity": None,
    "landed_at": None,
    "landed_commit": None,
    "linked_issues": [],
    "blocked_by": [],
    "signals": {
        "closed_without_merge": False,
        "closed_but_recorded_in_progress": False,
        "checks_failing_since": None,
        "unlinked": "never_linked",
    },
}


def _open_issue_node(number: int) -> dict:
    """A GraphQL issue node for an issue nobody has started."""
    return {
        "number": number,
        "state": "OPEN",
        "updatedAt": "2026-08-01T00:00:00Z",
        "labels": {"nodes": []},
        "timelineItems": {"nodes": []},
        "closedByPullRequestsReferences": {"nodes": []},
    }


def _merged_issue_node(number: int, pr_number: int) -> dict:
    """A GraphQL issue node for an issue closed by a merged pull request."""
    node = _open_issue_node(number)
    node["state"] = "CLOSED"
    node["closedByPullRequestsReferences"] = {
        "nodes": [
            {
                "number": pr_number,
                "state": "MERGED",
                "merged": True,
                "updatedAt": "2026-08-31T18:35:09Z",
                "isDraft": False,
                "commits": {"nodes": []},
            }
        ]
    }
    return node


def _mock_issue_activity(httpx_mock: HTTPXMock, *nodes: dict) -> None:
    """Register the batched issue-activity GraphQL response."""
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"repository": {f"i{node['number']}": node for node in nodes}}},
    )


def _mock_full_flow(httpx_mock: HTTPXMock, content: str = _SAMPLE_MD, sha: str = "sha1") -> None:
    """Register find_open_pr → list_files → get_file → update_file responses."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(content, sha))
    httpx_mock.add_response(json={"content": {}, "commit": {}})


async def test_link_issues_updates_frontmatter(
    httpx_mock: HTTPXMock,
) -> None:
    """github_issue is set to the first issue's number."""
    _mock_full_flow(httpx_mock)

    result = await link_issues("owner/repo", "001", _ISSUES)

    post = frontmatter.loads(result["content"])
    assert post["github_issue"] == 42
    assert post["updated"] == date.today().isoformat()


async def test_link_issues_replaces_issues_section(
    httpx_mock: HTTPXMock,
) -> None:
    """## Issues section is replaced with a formatted bullet list."""
    _mock_full_flow(httpx_mock)

    result = await link_issues("owner/repo", "001", _ISSUES)

    assert "- #42 \u2014 Set up JWT middleware" in result["content"]
    assert "- #43 \u2014 Build login endpoints" in result["content"]


async def test_link_issues_sends_correct_sha_to_update(
    httpx_mock: HTTPXMock,
) -> None:
    """update_file is called with the sha returned by get_file."""
    _mock_full_flow(httpx_mock, sha="abc999")

    await link_issues("owner/repo", "001", _ISSUES)

    update_request = httpx_mock.get_requests()[3]
    body = json.loads(update_request.content)
    assert body["sha"] == "abc999"


async def test_link_issues_raises_when_requirement_not_found(
    httpx_mock: HTTPXMock,
) -> None:
    """FileNotFoundError if no file in requirements/ starts with the id."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])

    with pytest.raises(FileNotFoundError, match="001"):
        await link_issues("owner/repo", "001", _ISSUES)


async def test_link_issues_found_on_pr_branch_writes_back_to_branch(
    httpx_mock: HTTPXMock,
) -> None:
    """When requirement lives on a PR branch, link_issues reads from and writes to that branch."""
    pr_branch = "lachesis/req-001-user-auth"
    # 1. find_open_pr → GET /pulls?state=open returns a PR with matching head
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": pr_branch},
                "html_url": "https://github.com/owner/repo/pull/1",
                "number": 1,
            }
        ]
    )
    # 2. list_files on PR branch → returns the requirement file
    httpx_mock.add_response(json=_LIST_RESP)
    # 3. get_file on PR branch
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "branchsha"))
    # 4. update_file
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await link_issues("owner/repo", "001", _ISSUES)

    # Content was correctly updated
    post = frontmatter.loads(result["content"])
    assert post["github_issue"] == 42
    assert result["pr"]["number"] == 1

    # update_file request must include branch in payload
    requests = httpx_mock.get_requests()
    update_request = requests[3]
    body = json.loads(update_request.content)
    assert body["branch"] == pr_branch
    assert body["sha"] == "branchsha"

    # list_files (request 1) and get_file (request 2) must use PR branch ref
    # Branch name is URL-encoded in the query string (slashes → %2F)
    pr_branch_encoded = pr_branch.replace("/", "%2F")
    assert f"ref={pr_branch_encoded}" in str(requests[1].url)
    assert f"ref={pr_branch_encoded}" in str(requests[2].url)


async def test_link_issues_raises_when_not_found_on_main_or_pr_branch(
    httpx_mock: HTTPXMock,
) -> None:
    """FileNotFoundError when file is absent from default branch and no matching open PR."""
    # find_open_pr → open PRs exist but none match lachesis/req-001-*
    httpx_mock.add_response(json=[{"head": {"ref": "lachesis/req-002-something-else"}}])
    # list_files on default branch → no match
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])

    with pytest.raises(FileNotFoundError, match="001"):
        await link_issues("owner/repo", "001", _ISSUES)


async def test_link_issues_empty_issues_list(
    httpx_mock: HTTPXMock,
) -> None:
    """Empty issues list clears ## Issues section."""
    _mock_full_flow(httpx_mock)

    result = await link_issues("owner/repo", "001", [])

    post = frontmatter.loads(result["content"])
    assert post.get("github_issue") is None
    assert "## Issues" in result["content"]
    # No bullet lines
    lines = result["content"].splitlines()
    issue_lines = [line for line in lines if line.startswith("- #")]
    assert issue_lines == []


async def test_link_issues_appends_section_when_missing(
    httpx_mock: HTTPXMock,
) -> None:
    """If ## Issues is absent, the section is appended at the end."""
    md_without_issues = """\
---
id: "001"
title: "User authentication"
status: "planned"
github_issue: null
created: "2026-05-11"
updated: "2026-05-11"
---

## Why

Some text.

## What

Other text.
"""
    _mock_full_flow(httpx_mock, content=md_without_issues)

    result = await link_issues("owner/repo", "001", _ISSUES)

    assert "## Issues" in result["content"]
    assert "- #42 \u2014 Set up JWT middleware" in result["content"]


# ---------------------------------------------------------------------------
# delete_requirement tests
# ---------------------------------------------------------------------------

_DONE_MD = """\
---
id: "001"
title: "User authentication"
status: "done"
github_issue: 42
created: "2026-05-11"
updated: "2026-05-11"
---

## Why

Users need to save their rankings.

## What

Implement JWT auth.
"""


def _mock_delete_flow(httpx_mock: HTTPXMock, content: str = _SAMPLE_MD, sha: str = "sha1") -> None:
    """Register find_open_pr \u2192 list_files \u2192 get_file \u2192 delete_file responses."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(content, sha))
    httpx_mock.add_response(status_code=200, json={"commit": {}, "content": None})


async def test_delete_requirement_returns_deleted_path(
    httpx_mock: HTTPXMock,
) -> None:
    """Returns {"deleted": path, "pr": None} on a deletion that reached the default branch."""
    _mock_delete_flow(httpx_mock)

    result = await delete_requirement("owner/repo", "001")

    assert result == {"deleted": _PATH, "pr": None}


async def test_delete_requirement_refuses_done_status(
    httpx_mock: HTTPXMock,
) -> None:
    """Raises ValueError with explanation when status is 'done'."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_DONE_MD, "sha1"))

    with pytest.raises(ValueError, match="done requirement"):
        await delete_requirement("owner/repo", "001")


async def test_delete_requirement_raises_when_not_found(
    httpx_mock: HTTPXMock,
) -> None:
    """FileNotFoundError if no file starts with the id prefix."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])

    with pytest.raises(FileNotFoundError, match="001"):
        await delete_requirement("owner/repo", "001")


async def test_delete_requirement_sends_correct_sha(
    httpx_mock: HTTPXMock,
) -> None:
    """delete_file is called with the sha returned by get_file."""
    import json as _json

    _mock_delete_flow(httpx_mock, sha="deadbeef")

    await delete_requirement("owner/repo", "001")

    delete_request = httpx_mock.get_requests()[3]
    body = _json.loads(delete_request.content)
    assert body["sha"] == "deadbeef"


async def test_delete_requirement_sends_correct_commit_message(
    httpx_mock: HTTPXMock,
) -> None:
    """Commit message references the requirement id."""
    import json as _json

    _mock_delete_flow(httpx_mock)

    await delete_requirement("owner/repo", "001")

    delete_request = httpx_mock.get_requests()[3]
    body = _json.loads(delete_request.content)
    assert "001" in body["message"]


# ---------------------------------------------------------------------------
# create_github_issues tests
# ---------------------------------------------------------------------------

_ISSUES_URL = "https://api.github.com/repos/owner/repo/issues"


@pytest.fixture(autouse=True)
def _repo_without_specs():
    """Spec references are covered in test_issue_spec_references.py; here the repo has none."""
    with patch("tools.specs.uses_specs", AsyncMock(return_value=False)):
        yield


@pytest.fixture(autouse=True)
def _no_notes():
    """Notes travelling with next_issue are covered by their own test below."""
    with patch("tools.requirements.db.list_notes", return_value=[]) as mock:
        yield mock


@pytest.fixture(autouse=True)
def _nobody_waiting_on_the_author():
    """Questions waiting on the author are covered in test_progress_reporting.py."""
    with patch("tools.requirements.github.list_waiting_issues", AsyncMock(return_value=[])):
        yield


async def test_create_github_issues_single(httpx_mock: HTTPXMock) -> None:
    """Single issue is created and result returned."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 100,
            "number": 42,
            "title": "Fix bug",
            "html_url": "https://github.com/owner/repo/issues/42",
            "node_id": "I_kwDOabc",
        },
    )

    result = await create_github_issues(
        "owner/repo",
        [{"title": "Fix bug", "body": "Details here"}],
    )

    assert result == [
        {
            "id": 100,
            "number": 42,
            "title": "Fix bug",
            "url": "https://github.com/owner/repo/issues/42",
            "blocked_by": [],
            "added_to_project": False,
            "ready": True,
            "placed": False,
        }
    ]


async def test_create_github_issues_multiple(httpx_mock: HTTPXMock) -> None:
    """Multiple issues are created sequentially."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 200,
            "number": 1,
            "title": "First",
            "html_url": "https://github.com/owner/repo/issues/1",
            "node_id": "I_1",
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 201,
            "number": 2,
            "title": "Second",
            "html_url": "https://github.com/owner/repo/issues/2",
            "node_id": "I_2",
        },
    )

    result = await create_github_issues(
        "owner/repo",
        [
            {"title": "First", "body": "Body 1"},
            {"title": "Second", "body": "Body 2"},
        ],
    )

    assert len(result) == 2
    assert result[0]["number"] == 1
    assert result[1]["number"] == 2


async def test_create_github_issues_empty_list(httpx_mock: HTTPXMock) -> None:
    """Empty input returns empty list without any API calls."""
    result = await create_github_issues("owner/repo", [])

    assert result == []
    assert len(httpx_mock.get_requests()) == 0


async def test_create_github_issues_with_labels(httpx_mock: HTTPXMock) -> None:
    """Labels are passed through to the API call."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 300,
            "number": 10,
            "title": "Labeled",
            "html_url": "https://github.com/owner/repo/issues/10",
            "node_id": "I_labeled",
        },
    )

    await create_github_issues(
        "owner/repo",
        [{"title": "Labeled", "body": "Body", "labels": ["bug", "urgent"]}],
    )

    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert body["labels"] == ["bug", "urgent"]


async def test_create_github_issues_without_labels(httpx_mock: HTTPXMock) -> None:
    """Missing labels key does not cause KeyError."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 400,
            "number": 11,
            "title": "No labels",
            "html_url": "https://github.com/owner/repo/issues/11",
            "node_id": "I_nolabels",
        },
    )

    result = await create_github_issues(
        "owner/repo",
        [{"title": "No labels", "body": "Body"}],
    )

    assert result[0]["number"] == 11


# ---------------------------------------------------------------------------
# create_github_issues — blocked_by wiring
# ---------------------------------------------------------------------------

_DEP_URL_TEMPLATE = "https://api.github.com/repos/owner/repo/issues/{n}/dependencies/blocked_by"


async def test_create_github_issues_wires_blocked_by_from_batch(httpx_mock: HTTPXMock) -> None:
    """B has blocked_by=[A.number]; add_issue_dependency called with A's integer id."""
    # Issue A created first
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 1001,
            "number": 1,
            "title": "A",
            "html_url": "https://github.com/owner/repo/issues/1",
            "node_id": "I_A",
        },
    )
    # Issue B created second
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 1002,
            "number": 2,
            "title": "B",
            "html_url": "https://github.com/owner/repo/issues/2",
            "node_id": "I_B",
        },
    )
    # Dependency wiring for B blocked by A
    httpx_mock.add_response(
        method="POST",
        url=_DEP_URL_TEMPLATE.format(n=2),
        status_code=200,
        json={},
    )

    result = await create_github_issues(
        "owner/repo",
        [
            {"title": "A", "body": "Issue A"},
            {"title": "B", "body": "Issue B", "blocked_by": [1]},
        ],
    )

    assert result[0]["number"] == 1
    assert result[0]["blocked_by"] == []
    assert result[1]["number"] == 2
    assert result[1]["blocked_by"] == [1]

    # Verify the dependency request sent the integer id of A (1001), not its number
    dep_request = httpx_mock.get_requests()[2]
    body = json.loads(dep_request.content)
    assert body == {"issue_id": 1001}


async def test_create_github_issues_fetches_external_blocker(httpx_mock: HTTPXMock) -> None:
    """blocked_by references a pre-existing issue not in the batch — GET fetched."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 2000,
            "number": 5,
            "title": "New",
            "html_url": "https://github.com/owner/repo/issues/5",
            "node_id": "I_new",
        },
    )
    # GET of pre-existing issue #3
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/owner/repo/issues/3",
        json={
            "id": 999,
            "number": 3,
            "title": "Old",
            "state": "open",
            "body": "Existing issue body",
            "assignees": [],
            "labels": [],
            "html_url": "https://github.com/owner/repo/issues/3",
        },
    )
    # Dependency POST
    httpx_mock.add_response(
        method="POST",
        url=_DEP_URL_TEMPLATE.format(n=5),
        status_code=200,
        json={},
    )

    result = await create_github_issues(
        "owner/repo",
        [{"title": "New", "body": "Body", "blocked_by": [3]}],
    )

    assert result[0]["blocked_by"] == [3]
    dep_request = httpx_mock.get_requests()[2]
    body = json.loads(dep_request.content)
    assert body == {"issue_id": 999}


async def test_create_github_issues_no_blocked_by_field_unchanged(httpx_mock: HTTPXMock) -> None:
    """Issues without blocked_by still work and return empty blocked_by list."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 100,
            "number": 7,
            "title": "No dep",
            "html_url": "https://github.com/owner/repo/issues/7",
            "node_id": "I_nodep",
        },
    )

    result = await create_github_issues(
        "owner/repo",
        [{"title": "No dep", "body": "Body"}],
    )

    assert result == [
        {
            "id": 100,
            "number": 7,
            "title": "No dep",
            "url": "https://github.com/owner/repo/issues/7",
            "blocked_by": [],
            "added_to_project": False,
            "ready": True,
            "placed": False,
        }
    ]


# ---------------------------------------------------------------------------
# create_requirement tests
# ---------------------------------------------------------------------------


async def test_create_requirement_returns_id_and_path(httpx_mock: HTTPXMock) -> None:
    """Returns id and path for the created requirement."""
    httpx_mock.add_response(json=_LIST_RESP)  # list_files → existing [001-user-auth.md]
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file

    result = await create_requirement("owner/repo", "New Feature", "Because X", "Do Y")

    assert result["id"] == "002"
    assert result["path"] == "requirements/002-new-feature.md"


async def test_create_requirement_empty_repo_assigns_001(httpx_mock: HTTPXMock) -> None:
    """When requirements/ is empty or absent, first ID is 001."""
    httpx_mock.add_response(json=[])  # list_files → no files
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await create_requirement("owner/repo", "First req", "Why", "What")

    assert result["id"] == "001"
    assert result["path"].startswith("requirements/001-")


async def test_create_requirement_slug_derived_from_title(httpx_mock: HTTPXMock) -> None:
    """Slug lowercases and hyphenates title."""
    httpx_mock.add_response(json=[])  # list_files
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await create_requirement("owner/repo", "Hello World!", "Why", "What")

    assert "hello-world" in result["path"]


async def test_create_requirement_sends_draft_status(httpx_mock: HTTPXMock) -> None:
    """Created file has status 'draft' and github_issue null."""
    httpx_mock.add_response(json=[])  # list_files
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    await create_requirement("owner/repo", "Test req", "Why text", "What text")

    create_request = httpx_mock.get_requests()[2]
    body = json.loads(create_request.content)
    decoded = base64.b64decode(body["content"]).decode("utf-8")
    post = frontmatter.loads(decoded)
    assert post["status"] == "draft"
    assert post["github_issue"] is None
    assert "## Why" in post.content
    assert "## What" in post.content
    assert "## Issues" in post.content
    assert "_None yet._" in post.content


async def test_create_requirement_increments_past_highest_id(httpx_mock: HTTPXMock) -> None:
    """ID increments past the highest existing numeric prefix, even with gaps."""
    existing = [
        {"name": "001-user-auth.md", "path": "requirements/001-user-auth.md"},
        {"name": "005-deploy.md", "path": "requirements/005-deploy.md"},
    ]
    httpx_mock.add_response(json=existing)  # list_files
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await create_requirement("owner/repo", "New Feature", "Why", "What")

    assert result["id"] == "006"
    assert result["path"] == "requirements/006-new-feature.md"


async def test_create_requirement_scans_pr_branches_for_id(httpx_mock: HTTPXMock) -> None:
    """ID assignment accounts for requirements in open lachesis/* PR branches."""
    # main has 001-user-auth.md
    httpx_mock.add_response(
        json=[{"name": "001-user-auth.md", "path": "requirements/001-user-auth.md"}]
    )  # list_files (main)
    # One open lachesis/* PR with 002-pending-feature.md on its branch
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": "lachesis/req-002-pending-feature"},
                "html_url": "https://github.com/owner/repo/pull/42",
                "number": 42,
            }
        ]
    )  # list_lachesis_prs → fetches open PRs from GitHub API
    httpx_mock.add_response(
        json=[{"name": "002-pending-feature.md", "path": "requirements/002-pending-feature.md"}]
    )  # list_files (PR branch)
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file

    result = await create_requirement("owner/repo", "New Feature", "Why", "What")

    # Must skip 002 (claimed by open PR) and assign 003
    assert result["id"] == "003"
    assert result["path"] == "requirements/003-new-feature.md"


async def test_create_requirement_slug_collision_via_pr_branch(httpx_mock: HTTPXMock) -> None:
    """Slug de-duplication considers filenames from PR branches as well as main."""
    httpx_mock.add_response(json=[])  # list_files (main) — empty
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": "lachesis/req-001-my-feature"},
                "html_url": "https://github.com/owner/repo/pull/1",
                "number": 1,
            }
        ]
    )  # list_lachesis_prs → fetches open PRs from GitHub API
    httpx_mock.add_response(
        json=[{"name": "001-my-feature.md", "path": "requirements/001-my-feature.md"}]
    )  # list_files (PR branch)
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file

    result = await create_requirement("owner/repo", "My Feature", "Why", "What")

    # ID is 002 (001 claimed by PR branch); slug must not collide with 001-my-feature.md
    assert result["id"] == "002"
    assert result["path"] == "requirements/002-my-feature.md"


async def test_create_requirement_pr_scan_http_error_falls_back_to_main_ids(
    httpx_mock: HTTPXMock,
) -> None:
    """When the PR-branch scan fails with an HTTP error, create_requirement
    proceeds using main-branch IDs only."""
    httpx_mock.add_response(
        json=[{"name": "001-auth.md", "path": "requirements/001-auth.md"}]
    )  # list_files (main)
    httpx_mock.add_response(
        status_code=500, json={"message": "Internal Server Error"}
    )  # list_lachesis_prs → HTTP error (triggers graceful fallback)
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file

    # Should succeed despite the PR scan failure, using main-only IDs
    result = await create_requirement("owner/repo", "New Feature", "Why", "What")
    assert result["id"] == "002"
    assert result["path"] == "requirements/002-new-feature.md"


async def test_create_requirement_falls_back_to_branch_pr_on_409(httpx_mock: HTTPXMock) -> None:
    """On 409 from create_file, creates branch and PR, returns pr_url."""
    httpx_mock.add_response(json=[])  # list_files → empty
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(
        status_code=409, json={"message": "Protected branch"}
    )  # create_file → 409
    httpx_mock.add_response(json={"object": {"sha": "deadbeef"}})  # get_branch_sha
    httpx_mock.add_response(status_code=201, json={})  # create_branch
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file on branch
    httpx_mock.add_response(
        status_code=201,
        json={"number": 5, "html_url": "https://github.com/owner/repo/pull/5", "node_id": "PR_abc"},
    )  # create_pull_request
    httpx_mock.add_response(
        json={"allow_auto_merge": True, "allow_squash_merge": True, "delete_branch_on_merge": True}
    )  # get_repo_settings
    httpx_mock.add_response(
        json={
            "data": {
                "enablePullRequestAutoMerge": {
                    "pullRequest": {"autoMergeRequest": {"enabledAt": "2026-01-01"}}
                }
            }
        }
    )  # GraphQL enable_automerge

    result = await create_requirement("owner/repo", "My Feature", "Why", "What")

    assert result["id"] == "001"
    assert result["path"] == "requirements/001-my-feature.md"
    assert result["pr_url"] == "https://github.com/owner/repo/pull/5"
    # request order: [0]=list_files, [1]=list_lachesis_prs, [2]=create_file(409),
    #                [3]=get_branch_sha, [4]=create_branch, [5]=create_file(branch),
    #                [6]=create_pr, [7]=get_repo_settings, [8]=enable_automerge
    branch_create_file_req = httpx_mock.get_requests()[5]
    body = json.loads(branch_create_file_req.content)
    assert body["branch"] == "lachesis/req-001-my-feature"


async def test_create_requirement_automerge_enabled_when_available(httpx_mock: HTTPXMock) -> None:
    """When repo allows automerge and squash, enablePullRequestAutoMerge is called."""
    httpx_mock.add_response(json=[])  # list_files
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(status_code=409, json={"message": "Protected"})
    httpx_mock.add_response(json={"object": {"sha": "abc"}})  # get_branch_sha
    httpx_mock.add_response(status_code=201, json={})  # create_branch
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file on branch
    httpx_mock.add_response(
        status_code=201,
        json={"number": 5, "html_url": "https://github.com/o/r/pull/5", "node_id": "PR_abc"},
    )  # create_pull_request
    httpx_mock.add_response(
        json={"allow_auto_merge": True, "allow_squash_merge": True, "delete_branch_on_merge": True}
    )  # get_repo_settings
    httpx_mock.add_response(
        json={
            "data": {
                "enablePullRequestAutoMerge": {
                    "pullRequest": {"autoMergeRequest": {"enabledAt": "2026-01-01"}}
                }
            }
        }
    )  # GraphQL

    result = await create_requirement("owner/repo", "My Feature", "Why", "What")

    assert result["pr_url"] == "https://github.com/o/r/pull/5"
    # Verify GraphQL request used SQUASH merge method
    graphql_req = httpx_mock.get_requests()[8]  # 9th request
    body = json.loads(graphql_req.content)
    assert body["variables"]["mergeMethod"] == "SQUASH"
    assert body["variables"]["pullRequestId"] == "PR_abc"


async def test_create_requirement_automerge_falls_back_gracefully_when_disabled(
    httpx_mock: HTTPXMock,
) -> None:
    """When repo has allow_auto_merge=False, returns pr_url without error."""
    httpx_mock.add_response(json=[])  # list_files
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(status_code=409, json={"message": "Protected"})
    httpx_mock.add_response(json={"object": {"sha": "abc"}})
    httpx_mock.add_response(status_code=201, json={})
    httpx_mock.add_response(json={"content": {}, "commit": {}})
    httpx_mock.add_response(
        status_code=201,
        json={"number": 5, "html_url": "https://github.com/o/r/pull/5", "node_id": "PR_abc"},
    )
    httpx_mock.add_response(
        json={
            "allow_auto_merge": False,
            "allow_squash_merge": True,
            "delete_branch_on_merge": False,
        }
    )  # get_repo_settings — automerge disabled

    result = await create_requirement("owner/repo", "My Feature", "Why", "What")

    assert result["pr_url"] == "https://github.com/o/r/pull/5"
    # Only 8 requests — no GraphQL mutation sent
    assert len(httpx_mock.get_requests()) == 8


async def test_create_requirement_automerge_exception_falls_back_to_pr_url(
    httpx_mock: HTTPXMock,
) -> None:
    """If get_repo_settings raises (HTTP 500), pr_url is still returned (graceful fallback)."""
    httpx_mock.add_response(json=[])  # list_files
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(status_code=409, json={"message": "Protected"})
    httpx_mock.add_response(json={"object": {"sha": "abc"}})  # get_branch_sha
    httpx_mock.add_response(status_code=201, json={})  # create_branch
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file on branch
    httpx_mock.add_response(
        status_code=201,
        json={"number": 5, "html_url": "https://github.com/o/r/pull/5", "node_id": "PR_abc"},
    )  # create_pull_request
    httpx_mock.add_response(status_code=500, json={})  # get_repo_settings fails

    result = await create_requirement("owner/repo", "My Feature", "Why", "What")

    # PR was already created — url must be returned despite the automerge failure
    assert result["pr_url"] == "https://github.com/o/r/pull/5"


async def test_create_requirement_automerge_uses_merge_when_squash_disabled(
    httpx_mock: HTTPXMock,
) -> None:
    """When allow_squash_merge=False and allow_merge_commit=True, mergeMethod is MERGE."""
    httpx_mock.add_response(json=[])  # list_files
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(status_code=409, json={"message": "Protected"})
    httpx_mock.add_response(json={"object": {"sha": "abc"}})  # get_branch_sha
    httpx_mock.add_response(status_code=201, json={})  # create_branch
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file on branch
    httpx_mock.add_response(
        status_code=201,
        json={"number": 5, "html_url": "https://github.com/o/r/pull/5", "node_id": "PR_abc"},
    )  # create_pull_request
    httpx_mock.add_response(
        json={
            "allow_auto_merge": True,
            "allow_squash_merge": False,
            "allow_merge_commit": True,
            "delete_branch_on_merge": True,
        }
    )  # get_repo_settings — squash disabled, merge allowed
    httpx_mock.add_response(
        json={
            "data": {
                "enablePullRequestAutoMerge": {
                    "pullRequest": {"autoMergeRequest": {"enabledAt": "2026-01-01"}}
                }
            }
        }
    )  # GraphQL

    result = await create_requirement("owner/repo", "My Feature", "Why", "What")

    assert result["pr_url"] == "https://github.com/o/r/pull/5"
    graphql_req = httpx_mock.get_requests()[8]  # 9th request
    body = json.loads(graphql_req.content)
    assert body["variables"]["mergeMethod"] == "MERGE"


async def test_create_requirement_non_409_error_propagates(httpx_mock: HTTPXMock) -> None:
    """Non-409 errors from create_file must propagate, not trigger the branch fallback."""
    httpx_mock.add_response(json=[])  # list_files
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(status_code=403, json={"message": "Forbidden"})  # create_file → 403

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await create_requirement("owner/repo", "My Feature", "Why", "What")
    assert exc_info.value.response.status_code == 403
    # Only 3 requests made — no branch/PR calls
    assert len(httpx_mock.get_requests()) == 3


async def test_create_requirement_no_pr_url_on_direct_write(httpx_mock: HTTPXMock) -> None:
    """When create_file succeeds directly, pr_url is absent from response."""
    httpx_mock.add_response(json=[])  # list_files
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file success

    result = await create_requirement("owner/repo", "Direct Write", "Why", "What")

    assert "pr_url" not in result


async def test_create_requirement_409_uses_correct_branch_name(httpx_mock: HTTPXMock) -> None:
    """Branch name follows lachesis/req-{id}-{slug} pattern."""
    httpx_mock.add_response(json=[])  # list_files
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(status_code=409, json={"message": "Protected branch"})
    httpx_mock.add_response(json={"object": {"sha": "abc"}})  # get_branch_sha
    httpx_mock.add_response(status_code=201, json={})  # create_branch — capture this
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file on branch
    httpx_mock.add_response(
        status_code=201,
        json={"number": 1, "html_url": "http://github.com/pr/1", "node_id": "PR_xyz"},
    )
    httpx_mock.add_response(
        json={"allow_auto_merge": True, "allow_squash_merge": True, "delete_branch_on_merge": True}
    )  # get_repo_settings
    httpx_mock.add_response(
        json={
            "data": {
                "enablePullRequestAutoMerge": {
                    "pullRequest": {"autoMergeRequest": {"enabledAt": "2026-01-01"}}
                }
            }
        }
    )  # GraphQL enable_automerge

    await create_requirement("owner/repo", "Hello World", "Why", "What")

    # 4th request (index 3) is get_branch_sha, 5th (index 4) is create_branch
    create_branch_req = httpx_mock.get_requests()[4]
    body = json.loads(create_branch_req.content)
    assert body["ref"] == "refs/heads/lachesis/req-001-hello-world"


# ---------------------------------------------------------------------------
# list_requirements tests
# ---------------------------------------------------------------------------


async def test_list_requirements_returns_metadata(httpx_mock: HTTPXMock) -> None:
    """Returns list of dicts with id, title, status, github_issue."""
    httpx_mock.add_response(json=_LIST_RESP)  # list_files
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))  # get_file
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs

    result = await list_requirements("owner/repo")

    assert result == [
        {
            "id": "001",
            "title": "User authentication",
            "status": "planned",
            "github_issue": None,
            "pr": None,
            "warning": "no linked issues",
            "derived": _UNLINKED_DERIVED,
        },
    ]


async def test_list_requirements_empty_repo(httpx_mock: HTTPXMock) -> None:
    """Returns [] when requirements/ is empty or absent."""
    httpx_mock.add_response(json=[])  # list_files → no files
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs

    result = await list_requirements("owner/repo")

    assert result == []
    assert len(httpx_mock.get_requests()) == 2  # list_files + list_lachesis_prs


async def test_list_requirements_multiple_files(httpx_mock: HTTPXMock) -> None:
    """Returns metadata for all .md files."""
    second_md = """\
---
id: "002"
title: "Data export"
status: "idea"
github_issue: 99
created: "2026-05-12"
updated: "2026-05-12"
---

## Why

Need exports.

## What

CSV export.
"""
    httpx_mock.add_response(
        json=[
            {"name": "001-user-auth.md", "path": "requirements/001-user-auth.md"},
            {"name": "002-data-export.md", "path": "requirements/002-data-export.md"},
        ]
    )
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(
        json={
            "name": "002-data-export.md",
            "path": "requirements/002-data-export.md",
            "content": base64.b64encode(second_md.encode()).decode("ascii"),
            "sha": "sha2",
            "type": "file",
        }
    )
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
    _mock_issue_activity(httpx_mock, _open_issue_node(99))

    result = await list_requirements("owner/repo")

    assert len(result) == 2
    assert result[1]["derived"]["source"] == "issues"
    assert result[0]["id"] == "001"
    assert result[0]["warning"] == "no linked issues"
    assert result[1]["id"] == "002"
    assert result[1]["github_issue"] == 99
    assert result[1]["warning"] is None


# ---------------------------------------------------------------------------
# get_requirement tests
# ---------------------------------------------------------------------------


async def test_get_requirement_returns_raw_content(httpx_mock: HTTPXMock) -> None:
    """Returns the raw markdown string including frontmatter."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=_LIST_RESP)  # list_files
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))  # get_file

    result = await get_requirement("owner/repo", "001")

    assert result == {"content": _SAMPLE_MD, "pr": None}


async def test_get_requirement_raises_when_not_found(httpx_mock: HTTPXMock) -> None:
    """FileNotFoundError if no file starts with the id prefix."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])

    with pytest.raises(FileNotFoundError, match="001"):
        await get_requirement("owner/repo", "001")


async def test_get_requirement_matches_by_id_prefix(httpx_mock: HTTPXMock) -> None:
    """Correctly finds a file by its numeric id prefix."""
    second_md = "---\nid: '002'\ntitle: Other\nstatus: idea\n---\nBody\n"
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(
        json=[
            {"name": "001-user-auth.md", "path": "requirements/001-user-auth.md"},
            {"name": "002-other.md", "path": "requirements/002-other.md"},
        ]
    )
    httpx_mock.add_response(
        json={
            "name": "002-other.md",
            "path": "requirements/002-other.md",
            "content": base64.b64encode(second_md.encode()).decode("ascii"),
            "sha": "sha2",
            "type": "file",
        }
    )

    result = await get_requirement("owner/repo", "002")

    assert result["content"] == second_md


# ---------------------------------------------------------------------------
# create_github_issues — project_id integration
# ---------------------------------------------------------------------------

_GRAPHQL_URL = "https://api.github.com/graphql"


async def test_create_github_issues_adds_to_project_when_project_id_set(
    httpx_mock: HTTPXMock,
) -> None:
    """When project_id is provided, add_to_project is called and added_to_project=True."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 100,
            "number": 42,
            "title": "Fix bug",
            "html_url": "https://github.com/owner/repo/issues/42",
            "node_id": "I_kwDOabc",
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"addProjectV2ItemById": {"item": {"id": "PVTI_x"}}}},
    )

    result = await create_github_issues(
        "owner/repo",
        [{"title": "Fix bug", "body": "Details"}],
        project_id="PVT_kwDO123",
    )

    assert result[0]["added_to_project"] is True
    graphql_req = httpx_mock.get_requests()[1]
    body = json.loads(graphql_req.content)
    assert body["variables"]["contentId"] == "I_kwDOabc"
    assert body["variables"]["projectId"] == "PVT_kwDO123"


async def test_create_github_issues_added_to_project_false_when_no_project_id(
    httpx_mock: HTTPXMock,
) -> None:
    """When project_id is None, added_to_project=False and no GraphQL call made."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 100,
            "number": 42,
            "title": "Fix bug",
            "html_url": "https://github.com/owner/repo/issues/42",
            "node_id": "I_kwDOabc",
        },
    )

    result = await create_github_issues(
        "owner/repo",
        [{"title": "Fix bug", "body": "Details"}],
    )

    assert result[0]["added_to_project"] is False
    assert len(httpx_mock.get_requests()) == 1  # only the issue POST, no GraphQL


async def test_create_github_issues_project_failure_does_not_abort(httpx_mock: HTTPXMock) -> None:
    """GraphQL failure is swallowed — issue still returned with added_to_project=False."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 100,
            "number": 42,
            "title": "Fix bug",
            "html_url": "https://github.com/owner/repo/issues/42",
            "node_id": "I_kwDOabc",
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        status_code=500,
        json={"message": "server error"},
    )

    result = await create_github_issues(
        "owner/repo",
        [{"title": "Fix bug", "body": "Details"}],
        project_id="PVT_kwDO123",
    )

    assert result[0]["number"] == 42
    assert result[0]["added_to_project"] is False


# ---------------------------------------------------------------------------
# list_requirements warning tests
# ---------------------------------------------------------------------------


async def test_list_requirements_no_warning_when_issue_linked(httpx_mock: HTTPXMock) -> None:
    """Requirements with github_issue set get warning=None."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_LINKED_MD, "sha1"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
    _mock_issue_activity(httpx_mock, _open_issue_node(42))

    result = await list_requirements("owner/repo")

    assert result[0]["github_issue"] == 42
    assert result[0]["warning"] is None
    assert result[0]["derived"]["source"] == "issues"
    assert result[0]["derived"]["linked_issues"] == [42]


# ---------------------------------------------------------------------------
# derived status tests
# ---------------------------------------------------------------------------


async def test_list_requirements_asks_github_nothing_when_no_issues_are_linked(
    httpx_mock: HTTPXMock,
) -> None:
    """Deriving nothing costs nothing — an unlinked repo makes no extra request."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs

    result = await list_requirements("owner/repo")

    assert result[0]["derived"]["source"] == "unlinked"
    assert not [r for r in httpx_mock.get_requests() if str(r.url) == _GRAPHQL_URL]


async def test_list_requirements_fetches_every_linked_issue_in_one_request(
    httpx_mock: HTTPXMock,
) -> None:
    """Issue activity is batched per repo, not fetched per requirement."""
    second_md = _BULLETED_MD.replace('id: "001"', 'id: "002"').replace("#43", "#44")
    httpx_mock.add_response(
        json=[
            {"name": _NAME, "path": _PATH},
            {"name": "002-data-export.md", "path": "requirements/002-data-export.md"},
        ]
    )
    httpx_mock.add_response(json=_make_file_response(_BULLETED_MD, "sha1"))
    httpx_mock.add_response(json=_make_second_file_response(second_md, "sha2"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
    _mock_issue_activity(
        httpx_mock, _open_issue_node(42), _open_issue_node(43), _open_issue_node(44)
    )

    result = await list_requirements("owner/repo")

    graphql_requests = [r for r in httpx_mock.get_requests() if str(r.url) == _GRAPHQL_URL]
    assert len(graphql_requests) == 1
    query = json.loads(graphql_requests[0].content)["query"]
    assert "i42: issue(number: 42)" in query
    assert "i44: issue(number: 44)" in query
    assert [r["derived"]["status"] for r in result] == ["idea", "idea"]


async def test_list_requirements_flags_a_draft_whose_issues_already_shipped(
    httpx_mock: HTTPXMock,
) -> None:
    """A requirement recorded as draft whose issues merged is reported as diverged."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_BULLETED_MD, "sha1"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
    _mock_issue_activity(httpx_mock, _merged_issue_node(42, 60), _merged_issue_node(43, 61))

    derived = (await list_requirements("owner/repo"))[0]["derived"]

    assert derived["status"] == "done"
    assert derived["diverged"] is True
    assert derived["linked_issues"] == [42, 43]
    assert derived["last_activity"] == "2026-08-31T18:35:09Z"


async def test_list_requirements_does_not_warn_about_bulleted_issues(
    httpx_mock: HTTPXMock,
) -> None:
    """Issues recorded only in the Issues section are still linked issues."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_BULLETED_MD, "sha1"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
    _mock_issue_activity(httpx_mock, _open_issue_node(42), _open_issue_node(43))

    result = await list_requirements("owner/repo")

    assert result[0]["github_issue"] is None
    assert result[0]["warning"] is None


async def test_list_requirements_degrades_when_issue_activity_fails(
    httpx_mock: HTTPXMock,
) -> None:
    """A listing is still a listing when the derivation cannot be made."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_BULLETED_MD, "sha1"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
    httpx_mock.add_response(method="POST", url=_GRAPHQL_URL, status_code=500, json={})

    result = await list_requirements("owner/repo")

    assert result[0]["id"] == "001"
    assert result[0]["derived"]["source"] == "unavailable"
    assert result[0]["derived"]["status"] is None
    assert result[0]["derived"]["unavailable_cause"] == "transient"


async def test_get_dashboard_keeps_the_repo_when_issue_activity_fails(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A failed derivation must not drop the whole repo out of the dashboard."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_BULLETED_MD, "sha1"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        httpx_mock.add_response(method="POST", url=_GRAPHQL_URL, status_code=500, json={})
        result = await get_dashboard()

    requirements = result["repos"][0]["requirements"]
    assert [r["id"] for r in requirements] == ["001"]
    assert requirements[0]["derived"]["source"] == "unavailable"


_SECOND_MD = """\
---
id: "002"
title: "Login endpoints"
status: "draft"
github_issue: null
created: "2026-05-11"
updated: "2026-05-11"
---

## Issues

- #99 — Filed against a number that turned out to be a pull request
"""

_TWO_FILE_LIST_RESP = [
    {"name": _NAME, "path": _PATH},
    {"name": "002-login.md", "path": "requirements/002-login.md"},
]


async def test_get_dashboard_counts_what_it_could_not_derive(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """An unread requirement is named in the summary, not silently counted as healthy."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_BULLETED_MD, "sha1"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        httpx_mock.add_response(method="POST", url=_GRAPHQL_URL, status_code=500, json={})
        result = await get_dashboard()

    summary = result["summary"]
    assert summary["unavailable_count"] == 1
    assert summary["unavailable_repos"] == [
        {"repo": "owner/repo", "count": 1, "causes": ["transient"]}
    ]
    # A derivation that failed leaves every count whole, which incomplete_repos does not.
    assert summary["incomplete_repos"] == []
    assert summary["total"] == 1


async def test_get_dashboard_reports_no_unavailable_when_everything_was_read(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """Both fields are present and empty rather than absent when nothing failed."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_BULLETED_MD, "sha1"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        httpx_mock.add_response(
            method="POST",
            url=_GRAPHQL_URL,
            json={
                "data": {"repository": {"i42": _open_issue_node(42), "i43": _open_issue_node(43)}}
            },
        )
        result = await get_dashboard()

    assert result["summary"]["unavailable_count"] == 0
    assert result["summary"]["unavailable_repos"] == []


async def test_get_dashboard_keeps_deriving_around_a_number_that_is_not_an_issue(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """One bad linked number costs its own requirement, not every sibling in the repo.

    GitHub answers the batch 200 with a null node and a NOT_FOUND naming that
    alias; treating that as a failed read marked the whole repo unavailable.
    """
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_TWO_FILE_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_BULLETED_MD, "sha1"))
        httpx_mock.add_response(json=_make_file_response(_SECOND_MD, "sha2"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        httpx_mock.add_response(
            method="POST",
            url=_GRAPHQL_URL,
            json={
                "data": {
                    "repository": {
                        "i42": _open_issue_node(42),
                        "i43": _open_issue_node(43),
                        "i99": None,
                    }
                },
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
        result = await get_dashboard()

    derived = {req["id"]: req["derived"] for req in result["repos"][0]["requirements"]}
    assert derived["001"]["source"] == "issues"
    assert derived["001"]["unavailable_cause"] is None
    assert derived["002"]["source"] == "unavailable"
    assert derived["002"]["unavailable_cause"] == "issue_missing"
    assert result["summary"]["unavailable_count"] == 1
    assert result["summary"]["unavailable_repos"] == [
        {"repo": "owner/repo", "count": 1, "causes": ["issue_missing"]}
    ]


async def test_get_dashboard_unlinked_count_excludes_bulleted_issues(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A requirement whose issues live only in its Issues section is not unlinked."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_BULLETED_MD, "sha1"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        _mock_issue_activity(httpx_mock, _open_issue_node(42), _open_issue_node(43))
        result = await get_dashboard()

    assert result["summary"]["unlinked_count"] == 0
    assert result["repos"][0]["requirements"][0]["derived"]["source"] == "issues"


# ---------------------------------------------------------------------------
# get_dashboard warning tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def set_db_user_id():
    """Set current_db_user_id for the test, reset afterwards."""
    token = current_db_user_id.set("test-uid")
    yield
    current_db_user_id.reset(token)


async def test_get_dashboard_requirement_warns_when_no_github_issue(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """Dashboard requirements with github_issue=null get warning='no linked issues'."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        result = await get_dashboard()

    req = result["repos"][0]["requirements"][0]
    assert req["warning"] == "no linked issues"


async def test_get_dashboard_summary_includes_unlinked_count(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """Dashboard summary includes unlinked_count for requirements without issues."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        result = await get_dashboard()

    assert result["summary"]["unlinked_count"] == 1


async def test_get_dashboard_unlinked_count_zero_when_all_linked(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """Dashboard unlinked_count is 0 when all requirements have github_issue set."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_LINKED_MD, "sha1"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        _mock_issue_activity(httpx_mock, _open_issue_node(42))
        result = await get_dashboard()

    assert result["summary"]["unlinked_count"] == 0
    assert result["repos"][0]["requirements"][0]["derived"]["source"] == "issues"


# ---------------------------------------------------------------------------
# update_requirement — draft status tests
# ---------------------------------------------------------------------------


async def test_update_requirement_accepts_draft_status(httpx_mock: HTTPXMock) -> None:
    """'draft' is accepted as a valid status value."""
    _mock_full_flow(httpx_mock)
    result = await update_requirement("owner/repo", "001", {"status": "draft"})
    post = frontmatter.loads(result["content"])
    assert post["status"] == "draft"


# ---------------------------------------------------------------------------
# link_issues — draft auto-advance tests
# ---------------------------------------------------------------------------


async def test_link_issues_advances_draft_to_idea(httpx_mock: HTTPXMock) -> None:
    """link_issues advances status from 'draft' to 'idea'."""
    draft_md = _SAMPLE_MD.replace('status: "planned"', 'status: "draft"')
    _mock_full_flow(httpx_mock, content=draft_md)
    result = await link_issues("owner/repo", "001", _ISSUES)
    post = frontmatter.loads(result["content"])
    assert post["status"] == "idea"


async def test_link_issues_does_not_change_non_draft_status(httpx_mock: HTTPXMock) -> None:
    """link_issues does not touch status when requirement is not 'draft'."""
    _mock_full_flow(httpx_mock)  # _SAMPLE_MD has status: "planned"
    result = await link_issues("owner/repo", "001", _ISSUES)
    post = frontmatter.loads(result["content"])
    assert post["status"] == "planned"


# ---------------------------------------------------------------------------
# Short-name resolution (_resolve_repo)
# ---------------------------------------------------------------------------


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


@pytest.mark.anyio
async def test_list_requirements_resolves_short_name(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """list_requirements('lachesis') resolves to 'alexsiri7/lachesis' via registered repos."""
    registered = [{"id": "r1", "repo_name": "alexsiri7/lachesis", "created_at": "t"}]
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs

    with patch("tools.requirements.db.list_repos", return_value=registered):
        result = await list_requirements("lachesis")

    assert len(result) == 1
    sent_url = str(httpx_mock.get_requests()[0].url)
    assert "alexsiri7/lachesis" in sent_url


@pytest.mark.anyio
async def test_list_requirements_short_name_case_insensitive(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """Short-name resolution is case-insensitive."""
    registered = [{"id": "r1", "repo_name": "alexsiri7/Lachesis", "created_at": "t"}]
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs

    with patch("tools.requirements.db.list_repos", return_value=registered):
        result = await list_requirements("lachesis")

    assert len(result) == 1


# ---------------------------------------------------------------------------
# get_comments / post_comment
# ---------------------------------------------------------------------------

_REGISTERED = [{"id": "r1", "repo_name": "alexsiri7/lachesis", "created_at": "t"}]
_ISSUE_160_COMMENTS_URL = re.compile(
    r"https://api\.github\.com/repos/alexsiri7/lachesis/issues/160/comments.*"
)


async def test_reading_a_workers_question(httpx_mock: HTTPXMock, set_user_id: str) -> None:
    """Scenario: Reading a worker's question."""
    httpx_mock.add_response(
        url=_ISSUE_160_COMMENTS_URL,
        json=[
            {
                "user": {"login": "worker-bot"},
                "created_at": "2026-09-20T09:00:00Z",
                "body": "Does the spec intend behaviour A or behaviour B?",
                "html_url": "https://github.com/alexsiri7/lachesis/issues/160#issuecomment-1",
            }
        ],
    )

    with patch("tools.requirements.db.list_repos", return_value=_REGISTERED):
        comments = await get_comments("lachesis", 160)

    assert comments == [
        {
            "author": "worker-bot",
            "created_at": "2026-09-20T09:00:00Z",
            "body": "Does the spec intend behaviour A or behaviour B?",
            "url": "https://github.com/alexsiri7/lachesis/issues/160#issuecomment-1",
        }
    ]


async def test_get_comments_raises_when_issue_missing(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    httpx_mock.add_response(url=_ISSUE_160_COMMENTS_URL, status_code=404)
    with (
        patch("tools.requirements.db.list_repos", return_value=_REGISTERED),
        pytest.raises(FileNotFoundError, match="#160"),
    ):
        await get_comments("alexsiri7/lachesis", 160)


async def test_post_comment_returns_new_comment_url(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.github.com/repos/alexsiri7/lachesis/issues/160/comments",
        status_code=201,
        json={"html_url": "https://github.com/alexsiri7/lachesis/issues/160#issuecomment-2"},
    )

    with patch("tools.requirements.db.list_repos", return_value=_REGISTERED):
        result = await post_comment("https://github.com/alexsiri7/Lachesis", 160, "Behaviour A.")

    assert result == {"url": "https://github.com/alexsiri7/lachesis/issues/160#issuecomment-2"}


async def test_post_comment_raises_when_issue_missing(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    httpx_mock.add_response(method="POST", url=_ISSUE_160_COMMENTS_URL, status_code=404)
    with (
        patch("tools.requirements.db.list_repos", return_value=_REGISTERED),
        pytest.raises(FileNotFoundError, match="#160"),
    ):
        await post_comment("alexsiri7/lachesis", 160, "Behaviour A.")


async def test_post_comment_rejects_empty_body(set_user_id: str) -> None:
    with pytest.raises(ValueError, match="body"):
        await post_comment("alexsiri7/lachesis", 160, "   ")


@pytest.mark.parametrize(
    "call",
    [
        lambda: get_comments("someone/else", 1),
        lambda: post_comment("someone/else", 1, "hello"),
    ],
    ids=["get_comments", "post_comment"],
)
async def test_comments_refused_for_unregistered_repository(call, set_user_id: str) -> None:
    with (
        patch("tools.requirements.db.list_repos", return_value=_REGISTERED),
        pytest.raises(ValueError, match="'someone/else' is not registered"),
    ):
        await call()


# ---------------------------------------------------------------------------
# update_github_issue tests
# ---------------------------------------------------------------------------

_UPDATE_ISSUE_URL = "https://api.github.com/repos/owner/repo/issues/42"


async def test_update_github_issue_returns_updated_fields(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="PATCH",
        url=_UPDATE_ISSUE_URL,
        json={
            "number": 42,
            "title": "Closed issue",
            "html_url": "https://github.com/owner/repo/issues/42",
            "state": "closed",
        },
    )
    result = await update_github_issue("owner/repo", 42, {"state": "closed"})
    assert result["number"] == 42
    assert result["title"] == "Closed issue"
    assert result["url"] == "https://github.com/owner/repo/issues/42"


async def test_update_github_issue_raises_on_not_found(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="PATCH",
        url=_UPDATE_ISSUE_URL,
        status_code=404,
    )
    with pytest.raises(FileNotFoundError, match="not found in"):
        await update_github_issue("owner/repo", 42, {"state": "closed"})


async def test_update_github_issue_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="Unknown fields"):
        await update_github_issue("owner/repo", 42, {"labels": ["bug"]})


async def test_update_github_issue_rejects_invalid_state() -> None:
    with pytest.raises(ValueError, match="Invalid state"):
        await update_github_issue("owner/repo", 42, {"state": "merged"})


async def test_update_github_issue_rejects_empty_fields() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        await update_github_issue("owner/repo", 42, {})


async def test_update_github_issue_rejects_empty_title() -> None:
    with pytest.raises(ValueError, match="title must not be empty"):
        await update_github_issue("owner/repo", 42, {"title": ""})


async def test_update_github_issue_rejects_whitespace_title() -> None:
    with pytest.raises(ValueError, match="title must not be empty"):
        await update_github_issue("owner/repo", 42, {"title": "   "})


async def test_update_github_issue_rejects_empty_body() -> None:
    with pytest.raises(ValueError, match="body must not be empty"):
        await update_github_issue("owner/repo", 42, {"body": ""})


# ---------------------------------------------------------------------------
# get_github_issue tests
# ---------------------------------------------------------------------------

_ISSUE_URL = "https://api.github.com/repos/owner/repo/issues/42"

_ISSUE_JSON = {
    "id": 1,
    "number": 42,
    "title": "Fix auth bug",
    "state": "open",
    "body": "Detailed description",
    "assignees": [{"login": "alice"}],
    "labels": [{"name": "bug"}],
    "pull_request": {"html_url": "https://github.com/owner/repo/pull/7"},
    "html_url": "https://github.com/owner/repo/issues/42",
}


@pytest.mark.anyio
async def test_get_github_issue_returns_issue_fields(httpx_mock: HTTPXMock) -> None:
    """Happy path: returns all expected fields extracted from GitHub API response."""
    httpx_mock.add_response(method="GET", url=_ISSUE_URL, json=_ISSUE_JSON)

    result = await get_github_issue("owner/repo", 42)

    assert result["number"] == 42
    assert result["title"] == "Fix auth bug"
    assert result["body"] == "Detailed description"
    assert result["assignees"] == ["alice"]
    assert result["labels"] == ["bug"]
    assert result["linked_pr"] == "https://github.com/owner/repo/pull/7"
    assert result["state"] == "open"
    assert result["url"] == "https://github.com/owner/repo/issues/42"


@pytest.mark.anyio
async def test_get_github_issue_raises_on_404(httpx_mock: HTTPXMock) -> None:
    """Raises FileNotFoundError when GitHub returns 404 for the issue."""
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/owner/repo/issues/99",
        status_code=404,
    )

    with pytest.raises(FileNotFoundError, match="Issue #99 not found"):
        await get_github_issue("owner/repo", 99)


# ---------------------------------------------------------------------------
# get_pull_request tests
# ---------------------------------------------------------------------------

_PULL_URL = "https://api.github.com/repos/owner/repo/pulls/7"

_PULL_JSON = {
    "number": 7,
    "title": "Add the thing",
    "html_url": "https://github.com/owner/repo/pull/7",
    "state": "open",
    "merged": False,
    "merged_at": None,
    "mergeable": True,
    "mergeable_state": "unstable",
    "head": {"ref": "feature/thing"},
    "base": {"ref": "main"},
    "draft": False,
}


@pytest.mark.anyio
async def test_get_pull_request_returns_pr_fields(httpx_mock: HTTPXMock) -> None:
    """Happy path: found=True alongside the fields extracted from the GitHub response."""
    httpx_mock.add_response(method="GET", url=_PULL_URL, json=_PULL_JSON)

    result = await get_pull_request("owner/repo", 7)

    assert result["found"] is True
    assert result["number"] == 7
    assert result["title"] == "Add the thing"
    assert result["url"] == "https://github.com/owner/repo/pull/7"
    assert result["state"] == "open"
    assert result["merged"] is False
    assert result["merged_at"] is None
    assert result["mergeable"] is True
    assert result["mergeable_state"] == "unstable"
    assert result["head_ref"] == "feature/thing"
    assert result["base_ref"] == "main"
    assert result["draft"] is False


@pytest.mark.anyio
async def test_get_pull_request_reports_not_found_without_raising(httpx_mock: HTTPXMock) -> None:
    """A missing pull request is a result, not an error."""
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/owner/repo/pulls/99",
        status_code=404,
    )

    result = await get_pull_request("owner/repo", 99)

    assert result == {"found": False, "number": 99, "repo": "owner/repo"}


@pytest.mark.anyio
async def test_get_pull_request_propagates_server_error(httpx_mock: HTTPXMock) -> None:
    """A 5xx is not a not-found result — it propagates."""
    httpx_mock.add_response(method="GET", url=_PULL_URL, status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        await get_pull_request("owner/repo", 7)


# ---------------------------------------------------------------------------
# get_dependencies tests
# ---------------------------------------------------------------------------

_GET_DEPS_BLOCKED_BY_URL = (
    "https://api.github.com/repos/owner/repo/issues/42/dependencies/blocked_by"
)
_GET_DEPS_BLOCKING_URL = "https://api.github.com/repos/owner/repo/issues/42/dependencies/blocking"


@pytest.mark.anyio
async def test_get_dependencies_returns_blocked_by_and_blocking(httpx_mock: HTTPXMock) -> None:
    """Happy path: returns structured blocked_by and blocking lists."""
    httpx_mock.add_response(
        method="GET", url=_GET_DEPS_BLOCKED_BY_URL, json=[{"number": 38, "id": 1001}]
    )
    httpx_mock.add_response(
        method="GET", url=_GET_DEPS_BLOCKING_URL, json=[{"number": 45, "id": 1003}]
    )

    result = await get_dependencies("owner/repo", 42)

    assert result["blocked_by"] == [38]
    assert result["blocking"] == [45]


@pytest.mark.anyio
async def test_get_dependencies_raises_on_404(httpx_mock: HTTPXMock) -> None:
    """Raises FileNotFoundError when GitHub returns 404 for the issue."""
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/owner/repo/issues/99/dependencies/blocked_by",
        status_code=404,
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/owner/repo/issues/99/dependencies/blocking",
        json=[],
    )

    with pytest.raises(FileNotFoundError, match="Issue #99 not found"):
        await get_dependencies("owner/repo", 99)


@pytest.mark.anyio
async def test_get_dependencies_propagates_http_error(httpx_mock: HTTPXMock) -> None:
    """Non-404 HTTP errors from GitHub must propagate as HTTPStatusError."""
    httpx_mock.add_response(
        method="GET",
        url=_GET_DEPS_BLOCKED_BY_URL,
        status_code=500,
        json={"message": "server error"},
    )
    httpx_mock.add_response(method="GET", url=_GET_DEPS_BLOCKING_URL, json=[])

    with pytest.raises(httpx.HTTPStatusError):
        await get_dependencies("owner/repo", 42)


@pytest.mark.anyio
async def test_get_dependencies_returns_empty_lists_when_no_deps(httpx_mock: HTTPXMock) -> None:
    """An issue with no dependencies returns empty blocked_by and blocking."""
    httpx_mock.add_response(method="GET", url=_GET_DEPS_BLOCKED_BY_URL, json=[])
    httpx_mock.add_response(method="GET", url=_GET_DEPS_BLOCKING_URL, json=[])

    result = await get_dependencies("owner/repo", 42)

    assert result == {"blocked_by": [], "blocking": []}


# ---------------------------------------------------------------------------
# add_dependency tests
# ---------------------------------------------------------------------------

_ADD_DEP_ISSUE_URL = "https://api.github.com/repos/owner/repo/issues/10"
_ADD_DEP_BLOCKER_URL = "https://api.github.com/repos/owner/repo/issues/5"
_ADD_DEP_POST_URL = "https://api.github.com/repos/owner/repo/issues/10/dependencies/blocked_by"
_ADD_DEP_GET_BLOCKED_BY_URL = (
    "https://api.github.com/repos/owner/repo/issues/10/dependencies/blocked_by"
)
_ADD_DEP_GET_BLOCKING_URL = (
    "https://api.github.com/repos/owner/repo/issues/10/dependencies/blocking"
)

_ADD_DEP_SUBJECT_JSON = {
    "id": 2000,
    "number": 10,
    "title": "Subject",
    "state": "open",
    "body": "",
    "assignees": [],
    "labels": [],
    "html_url": "https://github.com/owner/repo/issues/10",
}
_ADD_DEP_BLOCKER_JSON = {
    "id": 1001,
    "number": 5,
    "title": "Blocker",
    "state": "open",
    "body": "",
    "assignees": [],
    "labels": [],
    "html_url": "https://github.com/owner/repo/issues/5",
}


@pytest.mark.anyio
async def test_add_dependency_happy_path(httpx_mock: HTTPXMock) -> None:
    """Happy path: validates both issues, POSTs database id (not issue number), returns deps."""
    httpx_mock.add_response(method="GET", url=_ADD_DEP_ISSUE_URL, json=_ADD_DEP_SUBJECT_JSON)
    httpx_mock.add_response(method="GET", url=_ADD_DEP_BLOCKER_URL, json=_ADD_DEP_BLOCKER_JSON)
    httpx_mock.add_response(method="POST", url=_ADD_DEP_POST_URL, status_code=200, json={})
    httpx_mock.add_response(
        method="GET",
        url=_ADD_DEP_GET_BLOCKED_BY_URL,
        json=[{"number": 5, "id": 1001}],
    )
    httpx_mock.add_response(method="GET", url=_ADD_DEP_GET_BLOCKING_URL, json=[])

    result = await add_dependency("owner/repo", 10, 5)

    assert result == {"blocked_by": [5], "blocking": []}
    # Verify the POST body uses the database id, not the issue number
    post_req = [r for r in httpx_mock.get_requests() if r.method == "POST"][0]
    assert json.loads(post_req.content) == {"issue_id": 1001}


@pytest.mark.anyio
async def test_add_dependency_raises_when_subject_not_found(httpx_mock: HTTPXMock) -> None:
    """Raises FileNotFoundError when the subject issue does not exist."""
    httpx_mock.add_response(method="GET", url=_ADD_DEP_ISSUE_URL, status_code=404, json={})
    # blocker lookup runs in parallel; allow it to return anything
    httpx_mock.add_response(method="GET", url=_ADD_DEP_BLOCKER_URL, json=_ADD_DEP_BLOCKER_JSON)

    with pytest.raises(FileNotFoundError, match="Issue #10 not found"):
        await add_dependency("owner/repo", 10, 5)


@pytest.mark.anyio
async def test_add_dependency_raises_when_blocker_not_found(httpx_mock: HTTPXMock) -> None:
    """Raises FileNotFoundError when the blocked_by issue does not exist."""
    httpx_mock.add_response(method="GET", url=_ADD_DEP_ISSUE_URL, json=_ADD_DEP_SUBJECT_JSON)
    httpx_mock.add_response(method="GET", url=_ADD_DEP_BLOCKER_URL, status_code=404, json={})

    with pytest.raises(FileNotFoundError, match="Issue #5 not found"):
        await add_dependency("owner/repo", 10, 5)


@pytest.mark.anyio
async def test_add_dependency_raises_when_target_disappears_post_mutation(
    httpx_mock: HTTPXMock,
) -> None:
    """Raises FileNotFoundError when get_issue_dependencies returns None after the POST."""
    httpx_mock.add_response(method="GET", url=_ADD_DEP_ISSUE_URL, json=_ADD_DEP_SUBJECT_JSON)
    httpx_mock.add_response(method="GET", url=_ADD_DEP_BLOCKER_URL, json=_ADD_DEP_BLOCKER_JSON)
    httpx_mock.add_response(method="POST", url=_ADD_DEP_POST_URL, status_code=200, json={})
    httpx_mock.add_response(method="GET", url=_ADD_DEP_GET_BLOCKED_BY_URL, status_code=404)
    httpx_mock.add_response(method="GET", url=_ADD_DEP_GET_BLOCKING_URL, json=[])

    with pytest.raises(FileNotFoundError, match="Issue #10 not found"):
        await add_dependency("owner/repo", 10, 5)


# ---------------------------------------------------------------------------
# list_requirements — PR branch support
# ---------------------------------------------------------------------------


async def test_list_requirements_includes_pr_branch_requirements(httpx_mock: HTTPXMock) -> None:
    """Requirements on open PR branches appear in the list with their PR reported."""
    pr_branch = "lachesis/req-002-new-feature"
    pr_url = "https://github.com/owner/repo/pull/7"
    pr_md = """\
---
id: "002"
title: "New Feature"
status: "draft"
github_issue: null
created: "2026-08-01"
updated: "2026-08-01"
---

## Why

Need it.

## What

Do it.
"""
    # 1. list_files on main → returns 001 only
    httpx_mock.add_response(json=_LIST_RESP)
    # 2. get_file for 001 on main
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    # 3. list_lachesis_prs → GET /pulls?state=open
    httpx_mock.add_response(json=[{"head": {"ref": pr_branch}, "html_url": pr_url, "number": 7}])
    # 4. list_files on PR branch
    pr_file = {"name": "002-new-feature.md", "path": "requirements/002-new-feature.md"}
    httpx_mock.add_response(json=[pr_file])
    # 5. get_file for 002 on PR branch
    httpx_mock.add_response(
        json={
            "name": "002-new-feature.md",
            "path": "requirements/002-new-feature.md",
            "content": base64.b64encode(pr_md.encode()).decode("ascii"),
            "sha": "sha2",
            "type": "file",
        }
    )

    result = await list_requirements("owner/repo")

    assert len(result) == 2
    assert result[0]["id"] == "001"
    assert result[0]["pr"] is None  # read from main, so there is no PR to report
    assert result[1]["id"] == "002"
    assert result[1]["pr"] == {
        "number": 7,
        "state": "open",
        "merged": False,
        "url": pr_url,
        "branch": pr_branch,
        "created_at": None,
    }
    assert result[1]["warning"] == "awaiting merge of PR #7"
    assert result[0]["warning"] == "no linked issues"
    assert result[0]["derived"]["signals"]["unlinked"] == "never_linked"
    assert result[1]["derived"]["signals"]["unlinked"] == "awaiting_pr"


async def test_list_requirements_reports_a_requirements_own_pending_edit(
    httpx_mock: HTTPXMock,
) -> None:
    """A requirement's own open PR replaces main's copy: that is where its pending edit lives.

    Main still serves the pre-edit file until the PR merges, so the listing would
    otherwise disagree with what ``get_requirement`` reads for the same id.
    """
    pr_branch = "lachesis/req-001-user-auth-20260914T100000Z"
    pr_url_3 = "https://github.com/owner/repo/pull/3"
    renamed_md = _SAMPLE_MD.replace('title: "User authentication"', 'title: "Sign-in"')
    # 1. main → returns 001 under its old title
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    # 2. list_lachesis_prs → 001's own pending edit
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": pr_branch},
                "html_url": pr_url_3,
                "number": 3,
                "created_at": "2026-09-14T10:00:00Z",
            }
        ]
    )
    # 3. list_files on PR branch → the same path, read again from the branch
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(renamed_md, "branchsha"))

    result = await list_requirements("owner/repo")

    assert len(result) == 1
    assert result[0]["id"] == "001"
    assert result[0]["title"] == "Sign-in"
    assert result[0]["pr"] == {
        "number": 3,
        "state": "open",
        "merged": False,
        "url": pr_url_3,
        "branch": pr_branch,
        "created_at": "2026-09-14T10:00:00Z",
    }
    assert result[0]["warning"] == "awaiting merge of PR #3"
    assert f"ref={pr_branch.replace('/', '%2F')}" in str(httpx_mock.get_requests()[4].url)


async def test_list_requirements_keeps_the_first_listed_of_two_pull_requests_for_one_id(
    httpx_mock: HTTPXMock,
) -> None:
    """Two open PRs for one requirement resolve to the first listed, as get_requirement does."""
    first_branch = "lachesis/req-001-user-auth-20260914T110000Z"
    second_branch = "lachesis/req-001-user-auth-20260914T100000Z"
    first_md = _SAMPLE_MD.replace('title: "User authentication"', 'title: "Sign-in"')
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(
        json=[
            {"head": {"ref": first_branch}, "html_url": "https://x/pull/4", "number": 4},
            {"head": {"ref": second_branch}, "html_url": "https://x/pull/3", "number": 3},
        ]
    )
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(first_md, "sha-first"))
    httpx_mock.add_response(json=_LIST_RESP)  # the second branch's copy is not read

    result = await list_requirements("owner/repo")

    assert [(req["title"], req["pr"]["number"]) for req in result] == [("Sign-in", 4)]
    assert len(httpx_mock.get_requests()) == 6


async def test_list_requirements_no_prs_returns_main_only(httpx_mock: HTTPXMock) -> None:
    """When no open Lachesis PRs exist, result is same as before."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → empty

    result = await list_requirements("owner/repo")

    assert len(result) == 1
    assert result[0]["id"] == "001"


async def test_list_requirements_falls_back_to_main_when_the_pr_fetch_fails(
    httpx_mock: HTTPXMock,
) -> None:
    """A PR list that could not be read costs the PR branches, not the whole call."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(status_code=403, json={"message": "Forbidden"})

    result = await list_requirements("owner/repo")

    assert [entry["id"] for entry in result] == ["001"]


async def test_list_requirements_reports_the_pr_even_when_issues_are_linked(
    httpx_mock: HTTPXMock,
) -> None:
    """An unmerged PR outranks the issue link: merging it is what unblocks the rest."""
    pr_branch = "lachesis/req-002-new-feature"
    pr_md = _BULLETED_MD.replace('id: "001"', 'id: "002"')
    httpx_mock.add_response(json=[])  # list_files on main → nothing landed yet
    httpx_mock.add_response(
        json=[{"head": {"ref": pr_branch}, "html_url": "https://x/pull/7", "number": 7}]
    )
    pr_file = {"name": "002-new-feature.md", "path": "requirements/002-new-feature.md"}
    httpx_mock.add_response(json=[pr_file])
    httpx_mock.add_response(
        json={
            "name": pr_file["name"],
            "path": pr_file["path"],
            "content": base64.b64encode(pr_md.encode()).decode("ascii"),
            "sha": "sha2",
            "type": "file",
        }
    )
    _mock_issue_activity(httpx_mock, _open_issue_node(42), _open_issue_node(43))

    result = await list_requirements("owner/repo")

    assert result[0]["derived"]["linked_issues"] == [42, 43]
    assert result[0]["warning"] == "awaiting merge of PR #7"


async def test_list_requirements_looks_up_prs_once_for_the_whole_repo(
    httpx_mock: HTTPXMock,
) -> None:
    """PR state costs one request per repo, not one per requirement."""
    second_md = _SAMPLE_MD.replace('id: "001"', 'id: "002"')
    httpx_mock.add_response(
        json=[
            {"name": "001-user-auth.md", "path": "requirements/001-user-auth.md"},
            {"name": "002-other.md", "path": "requirements/002-other.md"},
        ]
    )
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(json=_make_file_response(second_md, "sha2"))
    httpx_mock.add_response(
        json=[
            {"head": {"ref": "lachesis/req-003-later"}, "html_url": "https://x/pull/9", "number": 9}
        ]
    )
    httpx_mock.add_response(json=[])  # list_files on the PR branch → nothing new

    result = await list_requirements("owner/repo")

    assert [entry["id"] for entry in result] == ["001", "002"]
    pr_lookups = [r for r in httpx_mock.get_requests() if "/pulls" in str(r.url)]
    assert len(pr_lookups) == 1
    assert "state=open" in str(pr_lookups[0].url)


# ---------------------------------------------------------------------------
# get_requirement — PR branch support
# ---------------------------------------------------------------------------


async def test_get_requirement_finds_on_pr_branch(httpx_mock: HTTPXMock) -> None:
    """get_requirement reads the file from its open PR branch."""
    pr_branch = "lachesis/req-001-user-auth"
    # 1. find_open_pr → GET /pulls?state=open
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": pr_branch},
                "html_url": "https://github.com/owner/repo/pull/1",
                "number": 1,
            }
        ]
    )
    # 2. list_files on PR branch → has 001
    httpx_mock.add_response(json=_LIST_RESP)
    # 3. get_file on PR branch
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))

    result = await get_requirement("owner/repo", "001")

    assert result["content"] == _SAMPLE_MD
    assert result["pr"] == {
        "number": 1,
        "state": "open",
        "merged": False,
        "url": "https://github.com/owner/repo/pull/1",
        "branch": pr_branch,
        "created_at": None,
    }
    # Verify branch ref used in list_files and get_file calls
    requests = httpx_mock.get_requests()
    pr_branch_encoded = pr_branch.replace("/", "%2F")
    assert f"ref={pr_branch_encoded}" in str(requests[1].url)
    assert f"ref={pr_branch_encoded}" in str(requests[2].url)


async def test_get_requirement_raises_when_not_on_main_or_any_pr(httpx_mock: HTTPXMock) -> None:
    """FileNotFoundError when not on main and no PR branch has the file."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])

    with pytest.raises(FileNotFoundError, match="001"):
        await get_requirement("owner/repo", "001")


# ---------------------------------------------------------------------------
# update_requirement — PR branch support
# ---------------------------------------------------------------------------


async def test_update_requirement_writes_to_pr_branch(httpx_mock: HTTPXMock) -> None:
    """update_requirement reads from and writes to the PR branch the requirement lives on."""
    pr_branch = "lachesis/req-001-user-auth"
    # 1. find_open_pr → GET /pulls?state=open
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": pr_branch},
                "html_url": "https://github.com/owner/repo/pull/1",
                "number": 1,
            }
        ]
    )
    # 2. list_files on PR branch
    httpx_mock.add_response(json=_LIST_RESP)
    # 3. get_file on PR branch
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "branchsha"))
    # 4. update_file
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await update_requirement("owner/repo", "001", {"status": "planned"})

    post = frontmatter.loads(result["content"])
    assert post["status"] == "planned"
    assert result["pr"]["number"] == 1

    requests = httpx_mock.get_requests()
    update_req = requests[3]
    body = json.loads(update_req.content)
    assert body["branch"] == pr_branch
    assert body["sha"] == "branchsha"


async def test_update_requirement_raises_when_not_found_on_main_or_pr(
    httpx_mock: HTTPXMock,
) -> None:
    """FileNotFoundError when not on main and no matching PR branch."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])

    with pytest.raises(FileNotFoundError, match="001"):
        await update_requirement("owner/repo", "001", {"status": "planned"})


# ---------------------------------------------------------------------------
# delete_requirement — PR branch support
# ---------------------------------------------------------------------------


async def test_delete_requirement_deletes_from_pr_branch(httpx_mock: HTTPXMock) -> None:
    """delete_requirement deletes from the PR branch the requirement lives on."""
    pr_branch = "lachesis/req-001-user-auth"
    # 1. find_open_pr → GET /pulls?state=open
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": pr_branch},
                "html_url": "https://github.com/owner/repo/pull/1",
                "number": 1,
            }
        ]
    )
    # 2. list_files on PR branch
    httpx_mock.add_response(json=_LIST_RESP)
    # 3. get_file on PR branch
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "branchsha"))
    # 4. delete_file
    httpx_mock.add_response(status_code=200, json={"commit": {}, "content": None})

    result = await delete_requirement("owner/repo", "001")

    assert result["deleted"] == _PATH
    assert result["pr"]["number"] == 1
    requests = httpx_mock.get_requests()
    delete_req = requests[3]
    body = json.loads(delete_req.content)
    assert body["branch"] == pr_branch
    assert body["sha"] == "branchsha"


async def test_delete_requirement_raises_when_not_found_on_main_or_pr(
    httpx_mock: HTTPXMock,
) -> None:
    """FileNotFoundError when not on main and no matching PR branch."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])

    with pytest.raises(FileNotFoundError, match="001"):
        await delete_requirement("owner/repo", "001")


# ---------------------------------------------------------------------------
# write conflicts on the PR branch
# ---------------------------------------------------------------------------

_PR_BRANCH = "lachesis/req-001-user-auth"
_CONFLICT_JSON = {"message": "requirements/001-user-auth.md does not match stalesha"}


def _mock_pr_branch_read(
    httpx_mock: HTTPXMock, sha: str = "branchsha", content: str = _SAMPLE_MD
) -> None:
    """Register the three GETs that locate a requirement on its open PR branch."""
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": _PR_BRANCH},
                "html_url": "https://github.com/owner/repo/pull/1",
                "number": 1,
            }
        ]
    )
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(content, sha))


_RENAMED_MD = _SAMPLE_MD.replace('title: "User authentication"', 'title: "Renamed upstream"')


def _written_post(request_body: dict) -> frontmatter.Post:
    """Decode the requirement a Contents API write carried."""
    return frontmatter.loads(base64.b64decode(request_body["content"]).decode())


async def test_link_issues_retries_a_conflicted_write_with_the_branch_sha(
    httpx_mock: HTTPXMock,
) -> None:
    """A 409 on the PR branch costs a re-read and another attempt, not the whole call."""
    _mock_pr_branch_read(httpx_mock, sha="stalesha")
    httpx_mock.add_response(status_code=409, json=_CONFLICT_JSON)
    _mock_pr_branch_read(httpx_mock, sha="freshsha", content=_RENAMED_MD)
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await link_issues("owner/repo", "001", _ISSUES)

    assert frontmatter.loads(result["content"])["github_issue"] == 42

    writes = [json.loads(r.content) for r in httpx_mock.get_requests() if r.method == "PUT"]
    assert [w["sha"] for w in writes] == ["stalesha", "freshsha"]
    assert {w["branch"] for w in writes} == {_PR_BRANCH}

    # The file exists only on the PR branch, so the retry's re-read must go back there:
    # every Contents API read of the locate, first and second, carries the branch ref.
    reads = [
        str(r.url)
        for r in httpx_mock.get_requests()
        if r.method == "GET" and "/contents/" in str(r.url)
    ]
    assert len(reads) == 4
    assert all(f"ref={_PR_BRANCH.replace('/', '%2F')}" in url for url in reads)

    # The retry rebuilds from what the second read returned, rather than resending the
    # first attempt's body under a newer sha.
    retried = _written_post(writes[1])
    assert retried["title"] == "Renamed upstream"
    assert retried["github_issue"] == 42


async def test_update_requirement_retries_a_conflicted_write_with_the_branch_sha(
    httpx_mock: HTTPXMock,
) -> None:
    """The re-read feeds the update, so the retry writes against the current sha."""
    _mock_pr_branch_read(httpx_mock, sha="stalesha")
    httpx_mock.add_response(status_code=409, json=_CONFLICT_JSON)
    _mock_pr_branch_read(httpx_mock, sha="freshsha", content=_RENAMED_MD)
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await update_requirement("owner/repo", "001", {"status": "idea"})

    assert frontmatter.loads(result["content"])["title"] == "Renamed upstream"

    writes = [json.loads(r.content) for r in httpx_mock.get_requests() if r.method == "PUT"]
    assert [w["sha"] for w in writes] == ["stalesha", "freshsha"]
    assert {w["branch"] for w in writes} == {_PR_BRANCH}

    retried = _written_post(writes[1])
    assert retried["title"] == "Renamed upstream"
    assert retried["status"] == "idea"


async def test_delete_requirement_retries_a_conflicted_delete_with_the_branch_sha(
    httpx_mock: HTTPXMock,
) -> None:
    """Deletes carry the same sha as writes do, so they recover the same way."""
    _mock_pr_branch_read(httpx_mock, sha="stalesha")
    httpx_mock.add_response(status_code=409, json=_CONFLICT_JSON)
    _mock_pr_branch_read(httpx_mock, sha="freshsha")
    httpx_mock.add_response(status_code=200, json={"commit": {}, "content": None})

    assert (await delete_requirement("owner/repo", "001"))["deleted"] == _PATH

    deletes = [json.loads(r.content) for r in httpx_mock.get_requests() if r.method == "DELETE"]
    assert [d["sha"] for d in deletes] == ["stalesha", "freshsha"]
    assert {d["branch"] for d in deletes} == {_PR_BRANCH}


async def test_delete_requirement_refuses_a_status_that_turned_done_before_the_retry(
    httpx_mock: HTTPXMock,
) -> None:
    """The retry judges the requirement it just re-read, so a freshly done one survives."""
    _mock_pr_branch_read(httpx_mock, sha="stalesha")
    httpx_mock.add_response(status_code=409, json=_CONFLICT_JSON)
    _mock_pr_branch_read(httpx_mock, sha="freshsha", content=_DONE_MD)

    with pytest.raises(ValueError, match="done requirement"):
        await delete_requirement("owner/repo", "001")

    deletes = [json.loads(r.content) for r in httpx_mock.get_requests() if r.method == "DELETE"]
    assert [d["sha"] for d in deletes] == ["stalesha"]


async def test_link_issues_stops_retrying_a_conflict_that_never_clears(
    httpx_mock: HTTPXMock,
) -> None:
    """Retries are bounded — a permanent PR-branch conflict is reported, not looped on."""
    for _ in range(_MAX_WRITE_ATTEMPTS):
        _mock_pr_branch_read(httpx_mock, sha="stalesha")
        httpx_mock.add_response(status_code=409, json=_CONFLICT_JSON)

    with pytest.raises(RuntimeError) as exc:
        await link_issues("owner/repo", "001", _ISSUES)

    # The message names the path and the branch and blames a concurrent change: every
    # attempt carried a freshly read sha, so Lachesis holding a stale version is excluded.
    assert _PATH in str(exc.value)
    assert _PR_BRANCH in str(exc.value)
    assert "concurrently" in str(exc.value)
    assert isinstance(exc.value.__cause__, httpx.HTTPStatusError)
    writes = [r for r in httpx_mock.get_requests() if r.method == "PUT"]
    assert len(writes) == _MAX_WRITE_ATTEMPTS
    assert not [r for r in httpx_mock.get_requests() if r.method == "POST"]


async def test_link_issues_does_not_retry_a_write_rejected_for_another_reason(
    httpx_mock: HTTPXMock,
) -> None:
    """Only a stale sha is worth re-reading for; a 422 is reported as it happened."""
    _mock_pr_branch_read(httpx_mock)
    httpx_mock.add_response(status_code=422, json={"message": "Invalid request"})

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await link_issues("owner/repo", "001", _ISSUES)

    assert exc.value.response.status_code == 422
    assert len([r for r in httpx_mock.get_requests() if r.method == "PUT"]) == 1


# ---------------------------------------------------------------------------
# write conflicts on a protected default branch
# ---------------------------------------------------------------------------

_PROTECTED_JSON = {
    "message": 'Could not update file: Required status check "Build & test" is expected.'
}
_FALLBACK_PR = {
    "number": 9,
    "html_url": "https://github.com/owner/repo/pull/9",
    "node_id": "PR_9",
    "created_at": "2026-09-14T10:00:00Z",
}


def _mock_protected_main_write(httpx_mock: HTTPXMock, *, method: str = "PUT") -> None:
    """Register every attempt against main answering 409 — the sha is fresh each time.

    The last refusal is followed by the lookup for a pull request already open.
    """
    for _ in range(_MAX_WRITE_ATTEMPTS):
        _mock_read_flow(httpx_mock)
        httpx_mock.add_response(method=method, status_code=409, json=_PROTECTED_JSON)
    httpx_mock.add_response(json=[])


def _mock_fallback_pull_request(httpx_mock: HTTPXMock, *, automerge: bool = True) -> None:
    """Register get_branch_sha → create_branch → get_file(branch) → update_file → PR → settings."""
    httpx_mock.add_response(json={"object": {"sha": "mainhead"}})
    httpx_mock.add_response(status_code=201, json={})
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "branchsha"))
    httpx_mock.add_response(method="PUT", json={"content": {}, "commit": {}})
    httpx_mock.add_response(method="POST", status_code=201, json=_FALLBACK_PR)
    httpx_mock.add_response(
        json={
            "allow_auto_merge": automerge,
            "allow_squash_merge": True,
            "delete_branch_on_merge": True,
        }
    )
    if automerge:
        httpx_mock.add_response(
            json={
                "data": {
                    "enablePullRequestAutoMerge": {
                        "pullRequest": {"autoMergeRequest": {"enabledAt": "2026-01-01"}}
                    }
                }
            }
        )


async def test_link_issues_lands_on_a_pull_request_when_main_keeps_refusing(
    httpx_mock: HTTPXMock,
) -> None:
    """A 409 that survives every fresh re-read on main is branch protection, not a stale sha."""
    _mock_protected_main_write(httpx_mock)
    _mock_fallback_pull_request(httpx_mock)

    result = await link_issues("owner/repo", "001", _ISSUES)

    assert frontmatter.loads(result["content"])["github_issue"] == 42
    assert result["pr"]["number"] == 9
    assert result["pr"]["url"] == "https://github.com/owner/repo/pull/9"
    assert result["pr"]["branch"].startswith("lachesis/req-001-user-auth-")
    assert result["pr"]["created_at"] == "2026-09-14T10:00:00Z"

    requests = httpx_mock.get_requests()
    writes = [json.loads(r.content) for r in requests if r.method == "PUT"]
    assert [w.get("branch") for w in writes[:_MAX_WRITE_ATTEMPTS]] == [None] * _MAX_WRITE_ATTEMPTS
    branch_write = writes[_MAX_WRITE_ATTEMPTS]
    assert branch_write["branch"] == result["pr"]["branch"]
    assert branch_write["sha"] == "branchsha"
    assert _written_post(branch_write)["github_issue"] == 42

    create_branch = json.loads(requests[-6].content)
    assert create_branch == {"ref": f"refs/heads/{result['pr']['branch']}", "sha": "mainhead"}
    create_pr = json.loads(requests[-3].content)
    assert create_pr["head"] == result["pr"]["branch"]
    assert create_pr["base"] == "main"
    assert create_pr["title"] == "[Lachesis] Link issues to requirement 001"
    assert "User authentication" in create_pr["body"]
    automerge = json.loads(requests[-1].content)
    assert automerge["variables"] == {"pullRequestId": "PR_9", "mergeMethod": "SQUASH"}


async def test_update_requirement_lands_on_a_pull_request_when_main_keeps_refusing(
    httpx_mock: HTTPXMock,
) -> None:
    """The update is rebuilt from the branch's own read and titled after the change."""
    _mock_protected_main_write(httpx_mock)
    _mock_fallback_pull_request(httpx_mock, automerge=False)

    result = await update_requirement("owner/repo", "001", {"status": "idea"})

    assert frontmatter.loads(result["content"])["status"] == "idea"
    assert result["pr"]["number"] == 9

    requests = httpx_mock.get_requests()
    branch_write = json.loads([r for r in requests if r.method == "PUT"][-1].content)
    assert branch_write["branch"] == result["pr"]["branch"]
    assert _written_post(branch_write)["status"] == "idea"
    create_pr = json.loads(requests[-2].content)
    assert create_pr["title"] == "[Lachesis] Update requirement 001"


async def test_delete_requirement_lands_on_a_pull_request_when_main_keeps_refusing(
    httpx_mock: HTTPXMock,
) -> None:
    """A deletion reports the pull request it awaits rather than claiming it is done."""
    _mock_protected_main_write(httpx_mock, method="DELETE")
    httpx_mock.add_response(json={"object": {"sha": "mainhead"}})
    httpx_mock.add_response(status_code=201, json={})
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "branchsha"))
    httpx_mock.add_response(method="DELETE", status_code=200, json={"commit": {}, "content": None})
    httpx_mock.add_response(method="POST", status_code=201, json=_FALLBACK_PR)
    httpx_mock.add_response(status_code=500, json={})  # get_repo_settings fails → manual merge

    result = await delete_requirement("owner/repo", "001")

    assert result["deleted"] == _PATH
    assert result["pr"]["number"] == 9

    deletes = [json.loads(r.content) for r in httpx_mock.get_requests() if r.method == "DELETE"]
    assert deletes[-1]["branch"] == result["pr"]["branch"]
    assert deletes[-1]["sha"] == "branchsha"


async def test_a_write_refuses_to_race_an_open_deletion_pull_request(
    httpx_mock: HTTPXMock,
) -> None:
    """Main answers for a requirement whose PR is deleting it; a refused write names that PR.

    Opening a second pull request from main's copy would race the deletion: whichever
    merges first leaves the other unmergeable, or silently undone.
    """
    deletion_branch = "lachesis/req-001-user-auth-20260914T100000Z"
    deletion_pr = {
        "head": {"ref": deletion_branch},
        "html_url": "https://github.com/owner/repo/pull/9",
        "number": 9,
    }
    for _ in range(_MAX_WRITE_ATTEMPTS):
        httpx_mock.add_response(json=[deletion_pr])
        httpx_mock.add_response(json=[])  # the deletion branch no longer lists the file
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(method="PUT", status_code=409, json=_PROTECTED_JSON)
    httpx_mock.add_response(json=[deletion_pr])

    with pytest.raises(RuntimeError) as exc:
        await update_requirement("owner/repo", "001", {"status": "idea"})

    message = str(exc.value)
    assert _PATH in message
    assert "#9" in message
    assert "https://github.com/owner/repo/pull/9" in message
    assert isinstance(exc.value.__cause__, httpx.HTTPStatusError)
    assert [r.method for r in httpx_mock.get_requests() if r.method != "GET"] == ["PUT"] * 3


async def test_a_second_write_joins_the_open_fallback_pull_request(
    httpx_mock: HTTPXMock,
) -> None:
    """While the fallback PR is open, later writes compound onto its branch, not onto main.

    Main still has the pre-edit file, so writing there again would 409 and open a
    competing PR whose base lacks the first change.
    """
    fallback_branch = "lachesis/req-001-user-auth-20260914T100000Z"
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": fallback_branch},
                "html_url": "https://github.com/owner/repo/pull/9",
                "number": 9,
            }
        ]
    )
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_LINKED_MD, "branchsha"))
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await update_requirement("owner/repo", "001", {"status": "idea"})

    assert result["pr"]["number"] == 9
    assert result["pr"]["branch"] == fallback_branch
    written = frontmatter.loads(result["content"])
    assert written["status"] == "idea"
    assert written["github_issue"] == 42

    requests = httpx_mock.get_requests()
    assert [r.method for r in requests] == ["GET", "GET", "GET", "PUT"]
    assert f"ref={fallback_branch.replace('/', '%2F')}" in str(requests[2].url)
    write = json.loads(requests[3].content)
    assert write["branch"] == fallback_branch
    assert write["sha"] == "branchsha"


async def test_get_requirement_reads_the_open_fallback_pull_request(
    httpx_mock: HTTPXMock,
) -> None:
    """A requirement with a pending update PR reads as that PR, not as main's older copy."""
    fallback_branch = "lachesis/req-001-user-auth-20260914T100000Z"
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": fallback_branch},
                "html_url": "https://github.com/owner/repo/pull/9",
                "number": 9,
                "created_at": "2026-09-14T10:00:00Z",
            }
        ]
    )
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_LINKED_MD, "branchsha"))

    result = await get_requirement("owner/repo", "001")

    assert result["content"] == _LINKED_MD
    assert result["pr"] == {
        "number": 9,
        "state": "open",
        "merged": False,
        "url": "https://github.com/owner/repo/pull/9",
        "branch": fallback_branch,
        "created_at": "2026-09-14T10:00:00Z",
    }


async def test_get_requirement_reads_main_while_a_deletion_pull_request_is_open(
    httpx_mock: HTTPXMock,
) -> None:
    """A fallback PR that removes the file does not make the requirement unreadable."""
    deletion_branch = "lachesis/req-001-user-auth-20260914T100000Z"
    httpx_mock.add_response(
        json=[
            {
                "head": {"ref": deletion_branch},
                "html_url": "https://github.com/owner/repo/pull/9",
                "number": 9,
            }
        ]
    )
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))

    result = await get_requirement("owner/repo", "001")

    assert result == {"content": _SAMPLE_MD, "pr": None}
    requests = httpx_mock.get_requests()
    assert f"ref={deletion_branch.replace('/', '%2F')}" in str(requests[1].url)
    assert "ref=" not in str(requests[2].url)
    assert "ref=" not in str(requests[3].url)


async def test_a_conflict_on_main_that_clears_never_opens_a_pull_request(
    httpx_mock: HTTPXMock,
) -> None:
    """A genuinely stale sha on main is still recovered by the re-read alone."""
    _mock_read_flow(httpx_mock)
    httpx_mock.add_response(status_code=409, json=_CONFLICT_JSON)
    _mock_full_flow(httpx_mock, sha="freshsha")

    result = await link_issues("owner/repo", "001", _ISSUES)

    assert result["pr"] is None
    writes = [json.loads(r.content) for r in httpx_mock.get_requests() if r.method == "PUT"]
    assert [w["sha"] for w in writes] == ["sha1", "freshsha"]
    assert not [r for r in httpx_mock.get_requests() if r.method == "POST"]


# ---------------------------------------------------------------------------
# get_dashboard — PR branch support
# ---------------------------------------------------------------------------


async def test_get_dashboard_includes_pr_branch_requirements(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """Dashboard includes requirements from open PR branches with their PR reported."""
    pr_branch = "lachesis/req-002-new-feature"
    pr_url = "https://github.com/owner/repo/pull/7"
    pr_md = (
        "---\nid: '002'\ntitle: New Feature\nstatus: draft\n"
        "github_issue: null\ncreated: '2026-08-01'\nupdated: '2026-08-01'\n---\n\nBody.\n"
    )
    with patch("tools.requirements.db.list_repos", return_value=[{"repo_name": "owner/repo"}]):
        # 1. list_files on main
        httpx_mock.add_response(json=_LIST_RESP)
        # 2. get_file for 001 on main
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        # 3. list_lachesis_prs
        httpx_mock.add_response(
            json=[{"head": {"ref": pr_branch}, "html_url": pr_url, "number": 7}]
        )
        # 4. list_files on PR branch
        httpx_mock.add_response(
            json=[{"name": "002-new-feature.md", "path": "requirements/002-new-feature.md"}]
        )
        # 5. get_file for 002 on PR branch
        httpx_mock.add_response(
            json={
                "name": "002-new-feature.md",
                "path": "requirements/002-new-feature.md",
                "content": base64.b64encode(pr_md.encode()).decode("ascii"),
                "sha": "sha2",
                "type": "file",
            }
        )
        result = await get_dashboard()

    reqs = result["repos"][0]["requirements"]
    assert len(reqs) == 2
    main_req = next(r for r in reqs if r["id"] == "001")
    pr_req = next(r for r in reqs if r["id"] == "002")
    assert main_req["pr"] is None
    assert pr_req["pr"] == {
        "number": 7,
        "state": "open",
        "merged": False,
        "url": pr_url,
        "branch": pr_branch,
        "created_at": None,
    }
    assert pr_req["warning"] == "awaiting merge of PR #7"
    assert main_req["warning"] == "no linked issues"
    assert main_req["derived"]["signals"]["unlinked"] == "never_linked"
    assert pr_req["derived"]["signals"]["unlinked"] == "awaiting_pr"
    assert result["summary"]["total"] == 2


# ---------------------------------------------------------------------------
# get_initial_instructions tests
# ---------------------------------------------------------------------------


async def test_get_initial_instructions_returns_string() -> None:
    """Returns a non-empty string."""
    result = await get_initial_instructions()
    assert isinstance(result, str)
    assert result.strip()


async def test_get_initial_instructions_mentions_required_tools() -> None:
    """Instructions reference all three required workflow tools."""
    result = await get_initial_instructions()
    assert "create_requirement" in result
    assert "create_github_issues" in result
    assert "link_issues" in result


async def test_get_initial_instructions_documents_parking() -> None:
    """Archon reads these at session start, so parking has to be discoverable."""
    result = await get_initial_instructions()

    assert "set_backlog_flags" in result
    assert "move_issue" in result
    assert "next_issue" in result
    assert "park" in result


async def test_get_initial_instructions_says_a_budget_question_blocks_nothing() -> None:
    result = await get_initial_instructions()

    assert "ask a budget\nquestion" in result
    assert "what the extra spend would deliver" in result
    assert "it blocks no issue" in result


async def test_stuck_on_intent() -> None:
    """Scenario: Stuck on intent — ask the author rather than guess, then move on."""
    result = await get_initial_instructions()

    assert "ask_author" in result
    assert "do not guess" in result
    assert "move on to other work" in result
    assert "answer_author" in result


async def test_guidance_says_to_leave_a_handoff_note() -> None:
    """Agents talk through notes on the work, so stopping on an issue leaves one."""
    result = await get_initial_instructions()

    assert "add_note" in result
    assert "handoff" in result
    assert "When you stop working on" in result


# ---------------------------------------------------------------------------
# Backlog ordering — get_backlog, move_issue, set_backlog_flags
# ---------------------------------------------------------------------------

_OPEN_ISSUES_URL = "https://api.github.com/repos/owner/repo/issues?state=open&per_page=100"


class _FakeBacklog:
    """In-memory stand-in for the lachesis_backlog accessors in db.py."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows: dict[int, dict] = {}
        for row in rows or []:
            self.rows[row["issue_number"]] = {
                "position": None,
                "ready": True,
                "interrupt": False,
                **row,
            }
        self.orders: list[list[int]] = []

    def _row(self, issue_number: int) -> dict:
        return self.rows.setdefault(
            issue_number,
            {
                "issue_number": issue_number,
                "position": None,
                "ready": True,
                "interrupt": False,
            },
        )

    def list_backlog(self, user_id: str, repo_name: str) -> list[dict]:
        return [dict(row) for row in self.rows.values()]

    def upsert_backlog_flags(
        self,
        user_id: str,
        repo_name: str,
        issue_number: int,
        ready: bool | None = None,
        interrupt: bool | None = None,
    ) -> dict:
        row = self._row(issue_number)
        if ready is not None:
            row["ready"] = ready
        if interrupt is not None:
            row["interrupt"] = interrupt
        return dict(row)

    def set_backlog_order(
        self, user_id: str, repo_name: str, ordered_issue_numbers: list[int]
    ) -> None:
        self.orders.append(list(ordered_issue_numbers))
        for row in self.rows.values():
            row["position"] = None
        for index, issue_number in enumerate(ordered_issue_numbers):
            self._row(issue_number)["position"] = index


@contextlib.contextmanager
def _patch_backlog(fake: _FakeBacklog):
    """Route the db backlog accessors at a _FakeBacklog for the duration of the block."""
    with (
        patch("db.list_backlog", fake.list_backlog),
        patch("db.upsert_backlog_flags", fake.upsert_backlog_flags),
        patch("db.set_backlog_order", fake.set_backlog_order),
    ):
        yield fake


def _mock_open_issues(
    httpx_mock: HTTPXMock,
    numbers: list[int],
    labels: dict[int, list[str]] | None = None,
    *,
    repo: str = "owner/repo",
) -> None:
    httpx_mock.add_response(
        url=f"https://api.github.com/repos/{repo}/issues?state=open&per_page=100",
        json=[
            {
                "number": n,
                "title": f"Issue {n}",
                "labels": [{"name": name} for name in (labels or {}).get(n, [])],
            }
            for n in numbers
        ],
        is_reusable=True,
    )


async def test_get_backlog_positioned_first_then_by_issue_number(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """Positioned rows lead; everything else follows in creation order."""
    _mock_open_issues(httpx_mock, [1, 2, 3])
    fake = _FakeBacklog([{"issue_number": 3, "position": 0}])

    with _patch_backlog(fake):
        result = await get_backlog("owner/repo")

    assert [(i["number"], i["position"]) for i in result] == [(3, 0), (1, 1), (2, 2)]


async def test_get_backlog_defaults_new_issues_to_ready(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """An issue with no row is ready — a row exists only to park or interrupt one."""
    _mock_open_issues(httpx_mock, [1])

    with _patch_backlog(_FakeBacklog()):
        result = await get_backlog("owner/repo")

    assert result == [
        {"number": 1, "title": "Issue 1", "position": 0, "ready": True, "interrupt": False}
    ]


async def test_get_backlog_omits_closed_issues_that_still_have_rows(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """A row for an issue that is no longer open drops out of the backlog."""
    _mock_open_issues(httpx_mock, [1])
    fake = _FakeBacklog(
        [{"issue_number": 2, "position": 0, "ready": True}, {"issue_number": 1, "position": 1}]
    )

    with _patch_backlog(fake):
        result = await get_backlog("owner/repo")

    assert [i["number"] for i in result] == [1]


async def test_get_backlog_puts_interrupt_first(httpx_mock: HTTPXMock, set_user_id: str) -> None:
    """An interrupt-marked issue leads the backlog whatever its position."""
    _mock_open_issues(httpx_mock, [1, 2, 3])
    fake = _FakeBacklog(
        [
            {"issue_number": 1, "position": 0},
            {"issue_number": 2, "position": 1},
            {"issue_number": 3, "position": 2, "interrupt": True},
        ]
    )

    with _patch_backlog(fake):
        result = await get_backlog("owner/repo")

    assert [i["number"] for i in result] == [3, 1, 2]
    assert result[0]["position"] == 2


async def test_move_issue_before_anchor(httpx_mock: HTTPXMock, set_user_id: str) -> None:
    _mock_open_issues(httpx_mock, [1, 2, 3])
    fake = _FakeBacklog()

    with _patch_backlog(fake):
        result = await move_issue("owner/repo", 3, before=2)

    assert fake.orders == [[1, 3, 2]]
    assert [i["number"] for i in result] == [1, 3, 2]
    assert "labels" not in result[0]


async def test_move_issue_after_anchor(httpx_mock: HTTPXMock, set_user_id: str) -> None:
    _mock_open_issues(httpx_mock, [1, 2, 3])
    fake = _FakeBacklog()

    with _patch_backlog(fake):
        await move_issue("owner/repo", 1, after=3)

    assert fake.orders == [[2, 3, 1]]


async def test_move_issue_requires_exactly_one_anchor(set_user_id: str) -> None:
    with pytest.raises(ValueError, match="exactly one of before/after"):
        await move_issue("owner/repo", 1)
    with pytest.raises(ValueError, match="exactly one of before/after"):
        await move_issue("owner/repo", 1, before=2, after=3)


async def test_move_issue_rejects_self_anchor(set_user_id: str) -> None:
    with pytest.raises(ValueError, match="relative to itself"):
        await move_issue("owner/repo", 1, before=1)


async def test_move_issue_unknown_anchor_raises(httpx_mock: HTTPXMock, set_user_id: str) -> None:
    _mock_open_issues(httpx_mock, [1, 2])

    with _patch_backlog(_FakeBacklog()):
        with pytest.raises(FileNotFoundError, match="#99"):
            await move_issue("owner/repo", 1, before=99)


async def test_move_issue_persists_base_order_not_interrupt_order(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """Moving an unrelated issue must not bake an interrupt's lead into its position."""
    _mock_open_issues(httpx_mock, [1, 2, 3])
    fake = _FakeBacklog(
        [
            {"issue_number": 1, "position": 0},
            {"issue_number": 2, "position": 1},
            {"issue_number": 3, "position": 2, "interrupt": True},
        ]
    )

    with _patch_backlog(fake):
        await move_issue("owner/repo", 2, before=1)

    # Had the move renumbered the effective order [3, 1, 2], issue 3 would land at 1.
    assert fake.orders == [[2, 1, 3]]
    assert fake.rows[3]["position"] == 2


async def test_set_backlog_flags_sets_ready_without_touching_interrupt(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/5",
        json={
            "number": 5,
            "id": 500,
            "title": "Five",
            "state": "open",
            "html_url": "https://github.com/owner/repo/issues/5",
        },
    )
    fake = _FakeBacklog([{"issue_number": 5, "interrupt": True}])

    with _patch_backlog(fake):
        result = await set_backlog_flags("owner/repo", 5, ready=True)

    assert result == {"number": 5, "ready": True, "interrupt": True}


async def test_set_backlog_flags_parks_an_existing_issue(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """Parking an already-filed issue is the flow the instructions direct Archon to."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/5",
        json={
            "number": 5,
            "id": 500,
            "title": "Five",
            "state": "open",
            "html_url": "https://github.com/owner/repo/issues/5",
        },
    )
    fake = _FakeBacklog()

    with _patch_backlog(fake):
        result = await set_backlog_flags("owner/repo", 5, ready=False)

    assert result["ready"] is False
    assert fake.rows[5]["ready"] is False


async def test_set_backlog_flags_requires_a_flag(set_user_id: str) -> None:
    with pytest.raises(ValueError, match="at least one of ready/interrupt"):
        await set_backlog_flags("owner/repo", 5)


async def test_set_backlog_flags_unknown_issue_raises(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    httpx_mock.add_response(status_code=404)

    with _patch_backlog(_FakeBacklog()):
        with pytest.raises(FileNotFoundError, match="#5"):
            await set_backlog_flags("owner/repo", 5, ready=True)


async def test_backlog_state_survives_an_issue_edit_outside_lachesis(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """Position and readiness live in Lachesis, so a GitHub-side edit cannot disturb them."""
    _mock_open_issues(httpx_mock, [1, 2])
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/owner/repo/issues/2",
        json={
            "number": 2,
            "id": 200,
            "title": "Two",
            "state": "open",
            "html_url": "https://github.com/owner/repo/issues/2",
        },
    )
    httpx_mock.add_response(
        method="PATCH",
        url="https://api.github.com/repos/owner/repo/issues/2",
        json={
            "number": 2,
            "title": "Retitled elsewhere",
            "html_url": "https://github.com/owner/repo/issues/2",
        },
    )
    fake = _FakeBacklog()

    with _patch_backlog(fake):
        await move_issue("owner/repo", 2, before=1)
        await set_backlog_flags("owner/repo", 2, ready=True)

        await update_github_issue("owner/repo", 2, {"title": "Retitled elsewhere"})

        result = await get_backlog("owner/repo")

    assert result[0]["number"] == 2
    assert result[0]["position"] == 0
    assert result[0]["ready"] is True


# ---------------------------------------------------------------------------
# create_github_issues — backlog overrides
# ---------------------------------------------------------------------------


def _mock_created_issue(httpx_mock: HTTPXMock, number: int) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": number * 100,
            "number": number,
            "title": f"Issue {number}",
            "html_url": f"https://github.com/owner/repo/issues/{number}",
            "node_id": f"I_{number}",
        },
    )


async def test_create_github_issues_without_overrides_never_touches_the_db(
    httpx_mock: HTTPXMock,
) -> None:
    """The default path is ready and last, which needs no row and so no DB call."""
    _mock_created_issue(httpx_mock, 1)
    backlog_db = MagicMock()

    with (
        patch("db.list_backlog", backlog_db.list_backlog),
        patch("db.upsert_backlog_flags", backlog_db.upsert_backlog_flags),
        patch("db.set_backlog_order", backlog_db.set_backlog_order),
    ):
        result = await create_github_issues("owner/repo", [{"title": "One", "body": "b"}])

    backlog_db.assert_not_called()
    assert backlog_db.mock_calls == []
    assert result[0]["ready"] is True
    assert result[0]["placed"] is False


async def test_create_github_issues_marks_ready_on_request(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    _mock_created_issue(httpx_mock, 1)
    fake = _FakeBacklog()

    with _patch_backlog(fake):
        result = await create_github_issues(
            "owner/repo", [{"title": "One", "body": "b", "ready": True}]
        )

    assert result[0]["ready"] is True
    assert fake.rows[1]["ready"] is True


async def test_create_github_issues_parks_on_explicit_false(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """Parking is the one thing creation cannot express by doing nothing."""
    _mock_created_issue(httpx_mock, 1)
    fake = _FakeBacklog()

    with _patch_backlog(fake):
        result = await create_github_issues(
            "owner/repo", [{"title": "One", "body": "b", "ready": False}]
        )

    assert result[0]["ready"] is False
    assert fake.rows[1]["ready"] is False


async def test_create_github_issues_reports_a_failed_park(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """A dropped park must be visible: the reported flag disagrees with the request."""
    _mock_created_issue(httpx_mock, 1)

    with (
        patch("db.list_backlog", return_value=[]),
        patch("db.upsert_backlog_flags", side_effect=RuntimeError("no db")),
    ):
        result = await create_github_issues(
            "owner/repo", [{"title": "One", "body": "b", "ready": False}]
        )

    assert result[0]["ready"] is True


async def test_create_github_issues_places_against_an_in_batch_anchor(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """An anchor naming an issue created earlier in the same batch resolves."""
    _mock_created_issue(httpx_mock, 1)
    _mock_created_issue(httpx_mock, 2)
    _mock_open_issues(httpx_mock, [1, 2, 3])
    fake = _FakeBacklog()

    with _patch_backlog(fake):
        result = await create_github_issues(
            "owner/repo",
            [
                {"title": "One", "body": "b"},
                {"title": "Two", "body": "b", "before": 1},
            ],
        )

    assert result[1]["placed"] is True
    assert fake.orders == [[2, 1, 3]]


async def test_create_github_issues_reports_an_unresolvable_anchor_without_raising(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """The issue already exists on GitHub, so a failed placement must not raise."""
    _mock_created_issue(httpx_mock, 1)
    _mock_open_issues(httpx_mock, [1])
    fake = _FakeBacklog()

    with _patch_backlog(fake):
        result = await create_github_issues(
            "owner/repo", [{"title": "One", "body": "b", "after": 99}]
        )

    assert result[0]["number"] == 1
    assert result[0]["placed"] is False
    assert fake.orders == []


# ---------------------------------------------------------------------------
# next_issue
# ---------------------------------------------------------------------------


def _mock_blocked_by(
    httpx_mock: HTTPXMock, issue_number: int, blockers: list[int], *, repo: str = "owner/repo"
) -> None:
    httpx_mock.add_response(
        url=f"https://api.github.com/repos/{repo}/issues/{issue_number}/dependencies/blocked_by",
        json=[{"number": n, "id": 1000 + n} for n in blockers],
    )


async def test_next_issue_returns_the_top_ready_unblocked_issue(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """The first unparked issue in backlog order wins; parked ones are passed over."""
    _mock_open_issues(httpx_mock, [1, 2, 3])
    _mock_blocked_by(httpx_mock, 2, [])
    fake = _FakeBacklog([{"issue_number": 1, "ready": False}])

    with _patch_backlog(fake):
        result = await next_issue("owner/repo")

    assert result["reason"] == "selected"
    assert result["issue"]["number"] == 2
    assert result["issue"]["repo"] == "owner/repo"
    assert result["blocked"] == []


async def test_next_issue_returns_the_offered_issues_notes_oldest_first(
    httpx_mock: HTTPXMock, set_user_id: str, _no_notes: MagicMock
) -> None:
    """Scenario: Picking up after a crash — the earlier run's handoff note comes along."""
    _mock_open_issues(httpx_mock, [160])
    _mock_blocked_by(httpx_mock, 160, [])
    notes = [
        {"id": 1, "issue": 160, "role": "implementer", "type": "progress", "text": "Started."},
        {"id": 4, "issue": 160, "role": "implementer", "type": "handoff", "text": "Half done."},
    ]
    _no_notes.return_value = notes

    with _patch_backlog(_FakeBacklog()):
        result = await next_issue("owner/repo")

    assert result["issue"]["number"] == 160
    assert result["notes"] == notes
    _no_notes.assert_called_once_with("test-uid", "owner/repo", issue=160)


async def test_next_issue_interrupt_beats_position(httpx_mock: HTTPXMock, set_user_id: str) -> None:
    """An interrupt-marked issue outranks a ready issue sitting at position 0."""
    _mock_open_issues(httpx_mock, [1, 2, 3])
    _mock_blocked_by(httpx_mock, 3, [])
    fake = _FakeBacklog(
        [
            {"issue_number": 1, "position": 0, "ready": True},
            {"issue_number": 3, "position": 2, "ready": True, "interrupt": True},
        ]
    )

    with _patch_backlog(fake):
        result = await next_issue("owner/repo")

    assert result["issue"]["number"] == 3
    assert result["issue"]["interrupt"] is True


async def test_next_issue_never_returns_a_blocked_issue(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """A ready issue with an open blocker is passed over and reported, not returned."""
    _mock_open_issues(httpx_mock, [1, 2, 3])
    _mock_blocked_by(httpx_mock, 1, [2])
    _mock_blocked_by(httpx_mock, 3, [])
    fake = _FakeBacklog([{"issue_number": 2, "ready": False}])

    with _patch_backlog(fake):
        result = await next_issue("owner/repo")

    assert result["issue"]["number"] == 3
    assert [(e["number"], [b["number"] for b in e["blocked_by"]]) for e in result["blocked"]] == [
        (1, [2])
    ]


async def test_next_issue_closed_blocker_does_not_block(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """A blocker outside the open set is looked up rather than assumed still open."""
    _mock_open_issues(httpx_mock, [1])
    _mock_blocked_by(httpx_mock, 1, [99])
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/99",
        json={
            "number": 99,
            "id": 1099,
            "title": "Done already",
            "state": "closed",
            "html_url": "https://github.com/owner/repo/issues/99",
        },
    )
    fake = _FakeBacklog([{"issue_number": 1, "ready": True}])

    with _patch_backlog(fake):
        result = await next_issue("owner/repo")

    assert result["issue"]["number"] == 1
    assert result["blocked"] == []


async def test_next_issue_open_blocker_outside_the_open_set_still_blocks(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """One page of open issues, so a blocker missing from it is looked up, not waived."""
    _mock_open_issues(httpx_mock, [1])
    _mock_blocked_by(httpx_mock, 1, [99])
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/99",
        json={
            "number": 99,
            "id": 1099,
            "title": "Still going",
            "state": "open",
            "html_url": "https://github.com/owner/repo/issues/99",
        },
    )
    fake = _FakeBacklog([{"issue_number": 1, "ready": True}])

    with _patch_backlog(fake):
        result = await next_issue("owner/repo")

    assert result["issue"] is None
    assert result["reason"] == "all_blocked"
    assert [b["number"] for b in result["blocked"][0]["blocked_by"]] == [99]


async def test_next_issue_reports_a_blocked_interrupt_rather_than_skipping_it(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """Two interrupts, the second blocked: the first is returned and the second still named."""
    _mock_open_issues(httpx_mock, [1, 2, 3])
    _mock_blocked_by(httpx_mock, 1, [])
    _mock_blocked_by(httpx_mock, 2, [3])
    fake = _FakeBacklog(
        [
            {"issue_number": 1, "position": 0, "ready": True, "interrupt": True},
            {"issue_number": 2, "position": 1, "ready": True, "interrupt": True},
        ]
    )

    with _patch_backlog(fake):
        result = await next_issue("owner/repo")

    assert result["issue"]["number"] == 1
    assert [(e["number"], e["interrupt"]) for e in result["blocked"]] == [(2, True)]


async def test_everything_blocked(httpx_mock: HTTPXMock, set_user_id: str) -> None:
    """Scenario: Everything blocked — the explanation names what is doing the blocking."""
    _mock_open_issues(httpx_mock, [1, 2])
    _mock_blocked_by(httpx_mock, 1, [2])
    fake = _FakeBacklog([{"issue_number": 2, "ready": False}])

    with _patch_backlog(fake):
        result = await next_issue("owner/repo")

    assert result["issue"] is None
    assert result["reason"] == "all_blocked"
    assert "#2" in result["explanation"]


async def test_next_issue_everything_parked(httpx_mock: HTTPXMock, set_user_id: str) -> None:
    """Open issues exist but every one is parked — unparking, not unblocking."""
    _mock_open_issues(httpx_mock, [1, 2])
    fake = _FakeBacklog([{"issue_number": 1, "ready": False}, {"issue_number": 2, "ready": False}])

    with _patch_backlog(fake):
        result = await next_issue("owner/repo")

    assert result["issue"] is None
    assert result["reason"] == "none_ready"
    assert "parked" in result["explanation"]


async def test_next_issue_returns_a_freshly_created_issue_with_no_backlog_row(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """A normal filing session hands the worker work with no follow-up call."""
    _mock_open_issues(httpx_mock, [1, 2])
    _mock_blocked_by(httpx_mock, 1, [])

    with _patch_backlog(_FakeBacklog()):
        result = await next_issue("owner/repo")

    assert result["reason"] == "selected"
    assert result["issue"]["number"] == 1


async def test_next_issue_empty_backlog(httpx_mock: HTTPXMock, set_user_id: str) -> None:
    """No open issues at all is its own answer."""
    _mock_open_issues(httpx_mock, [])

    with _patch_backlog(_FakeBacklog()):
        result = await next_issue("owner/repo")

    assert result["issue"] is None
    assert result["reason"] == "empty"
    assert "backlog is empty" in result["explanation"]


async def test_next_issue_all_in_progress(httpx_mock: HTTPXMock, set_user_id: str) -> None:
    """A ready, unblocked issue someone is already building is not work to hand out."""
    _mock_open_issues(httpx_mock, [1], {1: ["archon:in-progress"]})
    fake = _FakeBacklog([{"issue_number": 1, "ready": True}])

    with _patch_backlog(fake):
        result = await next_issue("owner/repo")

    assert result["issue"] is None
    assert result["reason"] == "all_in_progress"


async def test_skip_work_already_in_progress(httpx_mock: HTTPXMock, set_user_id: str) -> None:
    """Scenario: Skip work already in progress — it does not stop the walk."""
    _mock_open_issues(httpx_mock, [1, 2], {1: ["archon:in-progress"]})
    _mock_blocked_by(httpx_mock, 2, [])
    fake = _FakeBacklog([{"issue_number": 1, "ready": True}, {"issue_number": 2, "ready": True}])

    with _patch_backlog(fake):
        result = await next_issue("owner/repo")

    assert result["issue"]["number"] == 2


async def test_waiting_on_the_author(httpx_mock: HTTPXMock, set_user_id: str) -> None:
    """Scenario: Waiting on the author — skipped, then eligible again at its position."""
    fake = _FakeBacklog([{"issue_number": 1, "ready": True}, {"issue_number": 2, "ready": True}])

    _mock_open_issues(httpx_mock, [1, 2], {1: ["needs-author"]})
    _mock_blocked_by(httpx_mock, 2, [])
    with _patch_backlog(fake):
        waiting = await next_issue("owner/repo")
    assert waiting["issue"]["number"] == 2

    httpx_mock.reset()
    _mock_open_issues(httpx_mock, [1, 2])
    _mock_blocked_by(httpx_mock, 1, [])
    with _patch_backlog(fake):
        answered = await next_issue("owner/repo")
    assert answered["issue"]["number"] == 1
    assert answered["issue"]["position"] == 0


async def test_next_issue_all_waiting_on_the_author(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """Everything eligible waiting on the author is its own reason, not 'parked'."""
    _mock_open_issues(httpx_mock, [1, 2], {1: ["needs-author"], 2: ["archon:in-progress"]})
    fake = _FakeBacklog([{"issue_number": 1, "ready": True}, {"issue_number": 2, "ready": True}])

    with _patch_backlog(fake):
        result = await next_issue("owner/repo")

    assert result["issue"] is None
    assert result["reason"] == "all_waiting"
    assert "needs-author" in result["explanation"]


async def test_next_issue_interrupt_wins_across_repos(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """Interrupt is an authored override, so it outranks the repo tie-break."""
    _mock_open_issues(httpx_mock, [1])
    _mock_open_issues(httpx_mock, [5], repo="owner/other")
    _mock_blocked_by(httpx_mock, 1, [])
    _mock_blocked_by(httpx_mock, 5, [], repo="owner/other")
    fake = _FakeBacklog(
        [
            {"issue_number": 1, "ready": True},
            {"issue_number": 5, "ready": True, "interrupt": True},
        ]
    )
    registered = [{"repo_name": "owner/repo"}, {"repo_name": "owner/other"}]

    with _patch_backlog(fake), patch("tools.requirements.db.list_repos", return_value=registered):
        result = await next_issue()

    assert (result["issue"]["repo"], result["issue"]["number"]) == ("owner/other", 5)


async def test_next_issue_names_the_runners_up_it_passed_over(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """The cross-repo tie-break is arbitrary, so what it lost has to be visible."""
    _mock_open_issues(httpx_mock, [1])
    _mock_open_issues(httpx_mock, [5], repo="owner/other")
    _mock_blocked_by(httpx_mock, 1, [])
    _mock_blocked_by(httpx_mock, 5, [], repo="owner/other")
    fake = _FakeBacklog([{"issue_number": 1, "ready": True}, {"issue_number": 5, "ready": True}])
    registered = [{"repo_name": "owner/repo"}, {"repo_name": "owner/other"}]

    with _patch_backlog(fake), patch("tools.requirements.db.list_repos", return_value=registered):
        result = await next_issue()

    assert result["issue"]["repo"] == "owner/repo"
    assert result["also_eligible"] == [{"repo": "owner/other", "number": 5, "title": "Issue 5"}]


async def test_next_issue_scoped_call_omits_also_eligible(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """Asking about one repo answers about one repo — no runners-up from elsewhere."""
    _mock_open_issues(httpx_mock, [1])
    _mock_blocked_by(httpx_mock, 1, [])
    fake = _FakeBacklog([{"issue_number": 1, "ready": True}])
    registered = [{"repo_name": "owner/repo"}, {"repo_name": "owner/other"}]

    with _patch_backlog(fake), patch("tools.requirements.db.list_repos", return_value=registered):
        result = await next_issue("owner/repo")

    assert result["issue"]["number"] == 1
    assert "also_eligible" not in result


async def test_next_issue_with_no_registered_repos(set_user_id: str) -> None:
    """Nothing registered is not an empty backlog — the author's move is register_repo."""
    with _patch_backlog(_FakeBacklog()), patch("tools.requirements.db.list_repos", return_value=[]):
        result = await next_issue()

    assert result["issue"] is None
    assert result["reason"] == "empty"
    assert "registered" in result["explanation"]


async def test_next_issue_skips_a_repo_it_cannot_read(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """One unreachable repo must not cost the worker every other repo's backlog."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/gone/issues?state=open&per_page=100",
        status_code=404,
        json={},
    )
    _mock_open_issues(httpx_mock, [1])
    _mock_blocked_by(httpx_mock, 1, [])
    fake = _FakeBacklog([{"issue_number": 1, "ready": True}])
    registered = [{"repo_name": "owner/gone"}, {"repo_name": "owner/repo"}]

    with _patch_backlog(fake), patch("tools.requirements.db.list_repos", return_value=registered):
        result = await next_issue()

    assert result["issue"]["number"] == 1
    assert "owner/gone" in result["explanation"]


async def test_next_issue_scoped_call_surfaces_the_failure(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """Naming one repo makes its failure the answer, not something to degrade around."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/gone/issues?state=open&per_page=100",
        status_code=404,
        json={},
    )

    with _patch_backlog(_FakeBacklog()), pytest.raises(httpx.HTTPStatusError):
        await next_issue("owner/gone")


# ---------------------------------------------------------------------------
# Canonical status values
# ---------------------------------------------------------------------------

_VARIANT_MD = """\
---
id: "002"
title: "Data export"
status: "in_progress"
github_issue: 99
created: "2026-05-12"
updated: "2026-05-12"
---

## Why

Need exports.

## What

CSV export.
"""

_CANONICAL_MD = _SAMPLE_MD.replace('status: "planned"', 'status: "in-progress"')

_TWO_FILES_RESP = [
    {"name": _NAME, "path": _PATH},
    {"name": "002-data-export.md", "path": "requirements/002-data-export.md"},
]


def _make_second_file_response(content: str, sha: str) -> dict:
    """Build a Contents API file object for the second sample requirement."""
    return {
        "name": "002-data-export.md",
        "path": "requirements/002-data-export.md",
        "content": base64.b64encode(content.encode()).decode("ascii"),
        "sha": sha,
        "type": "file",
    }


async def test_get_dashboard_counts_variant_spelling_under_one_key(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A stored 'in_progress' is counted under 'in-progress', not as a second state."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_TWO_FILES_RESP)
        httpx_mock.add_response(json=_make_file_response(_CANONICAL_MD, "sha1"))
        httpx_mock.add_response(json=_make_second_file_response(_VARIANT_MD, "sha2"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        _mock_issue_activity(httpx_mock, _open_issue_node(99))
        result = await get_dashboard()

    assert result["summary"]["by_status"] == {"in-progress": 2}


def _mock_read_flow(httpx_mock: HTTPXMock, content: str = _SAMPLE_MD) -> None:
    """Register find_open_pr → list_files → get_file only, for updates expected to be rejected."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(content, "sha1"))


async def test_get_dashboard_rejects_a_variant_spelling_filter(set_db_user_id: None) -> None:
    """A caller filtering on 'in_progress' is told the canonical spelling, not given nothing."""
    with pytest.raises(ValueError, match="in-progress"):
        await get_dashboard(status="in_progress")


async def test_update_requirement_rejects_variant_spelling_with_a_hint(
    httpx_mock: HTTPXMock,
) -> None:
    """The variant spelling is rejected, and the message names the canonical form."""
    _mock_read_flow(httpx_mock)

    with pytest.raises(ValueError) as exc:
        await update_requirement("owner/repo", "001", {"status": "in_progress"})

    assert "in_progress" in str(exc.value)
    assert "in-progress" in str(exc.value)


async def test_link_issues_rewrites_a_stored_variant_spelling(httpx_mock: HTTPXMock) -> None:
    """A file rewritten for its issues comes back with the canonical status."""
    variant_md = _SAMPLE_MD.replace('status: "planned"', 'status: "in_progress"')
    _mock_full_flow(httpx_mock, content=variant_md)

    result = await link_issues("owner/repo", "001", _ISSUES)

    assert frontmatter.loads(result["content"])["status"] == "in-progress"


async def test_delete_requirement_refuses_a_differently_cased_done(httpx_mock: HTTPXMock) -> None:
    """Casing must not smuggle a done requirement past the deletion refusal."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD.replace("planned", "Done"), "sha1"))

    with pytest.raises(ValueError, match="done requirement"):
        await delete_requirement("owner/repo", "001")


# ---------------------------------------------------------------------------
# cancelled / superseded lifecycle states
# ---------------------------------------------------------------------------

_CANCELLED_MD = """\
---
id: "001"
title: "User authentication"
status: "cancelled"
reason: "Auth moved to the platform team"
github_issue: null
created: "2026-05-11"
updated: "2026-05-11"
---

## Why

Users need to save their rankings.
"""


async def test_cancel_without_a_reason(httpx_mock: HTTPXMock) -> None:
    """Cancelling without saying why is rejected."""
    _mock_read_flow(httpx_mock)

    with pytest.raises(ValueError, match="reason"):
        await update_requirement("owner/repo", "001", {"status": "cancelled"})


async def test_update_requirement_cancelled_stores_the_reason(httpx_mock: HTTPXMock) -> None:
    """A cancelled requirement records why it was dropped."""
    _mock_full_flow(httpx_mock)

    result = await update_requirement(
        "owner/repo", "001", {"status": "cancelled", "reason": "Superseded by platform auth"}
    )

    post = frontmatter.loads(result["content"])
    assert post["status"] == "cancelled"
    assert post["reason"] == "Superseded by platform auth"


async def test_update_requirement_keeps_an_existing_reason(httpx_mock: HTTPXMock) -> None:
    """Editing an already-cancelled requirement does not demand the reason again."""
    _mock_full_flow(httpx_mock, content=_CANCELLED_MD)

    result = await update_requirement("owner/repo", "001", {"title": "Renamed"})

    post = frontmatter.loads(result["content"])
    assert post["title"] == "Renamed"
    assert post["reason"] == "Auth moved to the platform team"


async def test_update_requirement_rejects_a_reason_on_a_live_status(
    httpx_mock: HTTPXMock,
) -> None:
    """A reason only means something on a cancelled requirement."""
    _mock_read_flow(httpx_mock)

    with pytest.raises(ValueError, match="'reason' is only valid"):
        await update_requirement("owner/repo", "001", {"status": "idea", "reason": "changed mind"})


async def test_update_requirement_drops_reason_when_revived(httpx_mock: HTTPXMock) -> None:
    """Reviving a cancelled requirement does not leave a stale reason behind."""
    _mock_full_flow(httpx_mock, content=_CANCELLED_MD)

    result = await update_requirement("owner/repo", "001", {"status": "idea"})

    post = frontmatter.loads(result["content"])
    assert post["status"] == "idea"
    assert "reason" not in post.metadata


async def test_update_requirement_superseded_requires_a_target(httpx_mock: HTTPXMock) -> None:
    """Superseding without saying by what is rejected."""
    _mock_read_flow(httpx_mock)

    with pytest.raises(ValueError, match="superseded_by"):
        await update_requirement("owner/repo", "001", {"status": "superseded"})


async def test_update_requirement_superseded_defaults_the_repo(httpx_mock: HTTPXMock) -> None:
    """A target without a repo means a requirement in this same repo."""
    _mock_full_flow(httpx_mock)

    result = await update_requirement(
        "owner/repo", "001", {"status": "superseded", "superseded_by": {"id": "030"}}
    )

    assert frontmatter.loads(result["content"])["superseded_by"] == {
        "repo": "owner/repo",
        "id": "030",
    }


async def test_update_requirement_superseded_pads_a_numeric_id(httpx_mock: HTTPXMock) -> None:
    """Requirement ids are zero-padded, so 11 means '011'."""
    _mock_full_flow(httpx_mock)

    result = await update_requirement(
        "owner/repo",
        "001",
        {"status": "superseded", "superseded_by": {"id": 11, "repo": "other/repo"}},
    )

    assert frontmatter.loads(result["content"])["superseded_by"] == {
        "repo": "other/repo",
        "id": "011",
    }


async def test_update_requirement_rejects_a_malformed_target(httpx_mock: HTTPXMock) -> None:
    """A replacement reference must be a mapping with an id."""
    _mock_read_flow(httpx_mock)

    with pytest.raises(ValueError, match=re.escape("got '030'")):
        await update_requirement(
            "owner/repo", "001", {"status": "superseded", "superseded_by": "030"}
        )


@pytest.mark.parametrize("raw_id", [None, "", "  "])
async def test_update_requirement_rejects_a_target_without_an_id(
    httpx_mock: HTTPXMock, raw_id: str | None
) -> None:
    """A requirement reference names the requirement it points at."""
    _mock_read_flow(httpx_mock)

    with pytest.raises(ValueError, match=re.escape(f"'id', got {raw_id!r}")):
        await update_requirement(
            "owner/repo", "001", {"status": "superseded", "superseded_by": {"id": raw_id}}
        )


async def test_supersede_across_repositories(httpx_mock: HTTPXMock) -> None:
    """The replacement is recorded, and the superseded requirement leaves default listings."""
    _mock_full_flow(httpx_mock)
    result = await update_requirement(
        "owner/repo",
        "001",
        {"status": "superseded", "superseded_by": {"id": "030", "repo": "other/repo"}},
    )
    assert frontmatter.loads(result["content"])["superseded_by"] == {
        "repo": "other/repo",
        "id": "030",
    }

    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(result["content"], "sha2"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs

    assert await list_requirements("owner/repo") == []


async def test_superseded_by_a_spec(httpx_mock: HTTPXMock) -> None:
    """A spec change, named by its pull request, can replace a requirement."""
    _mock_full_flow(httpx_mock)
    result = await update_requirement(
        "owner/repo",
        "001",
        {"status": "superseded", "superseded_by": {"spec_change": 156, "repo": "owner/repo"}},
    )
    replacement = {"repo": "owner/repo", "spec_change": 156}
    assert frontmatter.loads(result["content"])["superseded_by"] == replacement

    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(result["content"], "sha2"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs

    [listed] = await list_requirements("owner/repo", include_inactive=True)
    assert listed["superseded_by"] == replacement


@pytest.mark.parametrize(
    ("target", "rejected"),
    [
        ({"id": "030", "spec_change": 156}, {"id": "030", "spec_change": 156}),
        ({"spec_change": "156"}, "156"),
        ({"spec_change": True}, True),
        ({"spec_change": 0}, 0),
    ],
)
async def test_update_requirement_rejects_a_malformed_spec_change_target(
    httpx_mock: HTTPXMock, target: dict[str, object], rejected: object
) -> None:
    """A spec change is named by one positive pull request number, and never alongside an id."""
    _mock_read_flow(httpx_mock)

    with pytest.raises(ValueError, match=re.escape(f"got {rejected!r}")):
        await update_requirement(
            "owner/repo", "001", {"status": "superseded", "superseded_by": target}
        )


async def test_list_requirements_omits_cancelled_by_default(httpx_mock: HTTPXMock) -> None:
    """A cancelled requirement is not part of the active listing."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_CANCELLED_MD, "sha1"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs

    assert await list_requirements("owner/repo") == []


async def test_list_requirements_includes_cancelled_on_request(httpx_mock: HTTPXMock) -> None:
    """include_inactive returns the cancelled requirement, carrying its reason."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_CANCELLED_MD, "sha1"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs

    result = await list_requirements("owner/repo", include_inactive=True)

    assert len(result) == 1
    assert result[0]["status"] == "cancelled"
    assert result[0]["reason"] == "Auth moved to the platform team"


def _mock_cancelled_dashboard(httpx_mock: HTTPXMock) -> None:
    """One active and one cancelled requirement in a single registered repo."""
    httpx_mock.add_response(json=_TWO_FILES_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(
        json=_make_second_file_response(_CANCELLED_MD.replace('id: "001"', 'id: "002"'), "sha2")
    )
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs


async def test_get_dashboard_omits_cancelled_but_still_counts_it(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """The listing drops a cancelled requirement; the summary still totals it."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        _mock_cancelled_dashboard(httpx_mock)
        result = await get_dashboard()

    assert [r["id"] for r in result["repos"][0]["requirements"]] == ["001"]
    assert result["summary"]["by_status"] == {"planned": 1, "cancelled": 1}
    assert result["summary"]["total"] == 2


async def test_get_dashboard_includes_cancelled_on_request(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """include_inactive brings the cancelled requirement into the listing."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        _mock_cancelled_dashboard(httpx_mock)
        result = await get_dashboard(include_inactive=True)

    assert [r["id"] for r in result["repos"][0]["requirements"]] == ["001", "002"]


async def test_get_dashboard_status_filter_overrides_the_exclusion(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """Asking for cancelled requirements returns them without needing the flag."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        _mock_cancelled_dashboard(httpx_mock)
        result = await get_dashboard(status="cancelled")

    requirements = result["repos"][0]["requirements"]
    assert [r["id"] for r in requirements] == ["002"]
    assert requirements[0]["reason"] == "Auth moved to the platform team"


# ---------------------------------------------------------------------------
# get_dashboard groups
# ---------------------------------------------------------------------------


_IDEA_BULLETED_MD = _BULLETED_MD.replace('status: "draft"', 'status: "idea"')


def _quiet_since(days: int) -> str:
    """A GitHub timestamp exactly *days* old, so days_quiet is assertable."""
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mock_quiet_dashboard(httpx_mock: HTTPXMock, days: int) -> None:
    """One requirement whose two linked issues last moved *days* ago."""
    stamp = _quiet_since(days)
    nodes = []
    for number in (42, 43):
        node = _open_issue_node(number)
        node["updatedAt"] = stamp
        nodes.append(node)
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_IDEA_BULLETED_MD, "sha1"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
    _mock_issue_activity(httpx_mock, *nodes)


async def test_get_dashboard_reports_every_group_even_when_empty(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A repo with no requirements still answers all nine questions."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=[])  # list_files → no requirements
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        result = await get_dashboard()

    assert result["groups"] == {
        "landed": [],
        "dropped": [],
        "awaiting_merge": [],
        "stalled": [],
        "needs_attention": [],
        "unreadable": [],
        "never_linked": [],
        "diverged": [],
        "in_flight": [],
    }
    assert result["summary"]["groups"] == dict.fromkeys(result["groups"], 0)


async def test_get_dashboard_groups_a_cancelled_requirement_it_does_not_list(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """'What was dropped' is answerable without asking for inactive requirements."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        _mock_cancelled_dashboard(httpx_mock)
        result = await get_dashboard()

    assert [r["id"] for r in result["repos"][0]["requirements"]] == ["001"]
    assert [entry["id"] for entry in result["groups"]["dropped"]] == ["002"]
    assert result["groups"]["dropped"][0]["reason"] == "Auth moved to the platform team"


async def test_get_dashboard_reports_long_silence_as_stalled(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """Work nobody has touched since well before the threshold is stalled."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        _mock_quiet_dashboard(httpx_mock, days=100)
        result = await get_dashboard()

    assert [entry["id"] for entry in result["groups"]["stalled"]] == ["001"]
    assert result["groups"]["stalled"][0]["days_quiet"] == 100
    assert result["summary"]["stale_after_days"] == 14


async def test_get_dashboard_stale_after_days_moves_the_threshold(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """The same silence is unremarkable when the caller allows for it."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        _mock_quiet_dashboard(httpx_mock, days=100)
        result = await get_dashboard(stale_after_days=365)

    assert result["groups"]["stalled"] == []
    assert [entry["id"] for entry in result["groups"]["in_flight"]] == ["001"]
    assert result["summary"]["stale_after_days"] == 365


async def test_get_dashboard_rejects_a_threshold_below_one_day(set_db_user_id: None) -> None:
    """A threshold of zero would report every requirement as stalled."""
    with pytest.raises(ValueError, match="stale_after_days"):
        await get_dashboard(stale_after_days=0)


async def test_get_dashboard_groups_span_every_registered_repo(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """One call answers across repos, and each entry says where it came from."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}, {"repo_name": "owner/other"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        _mock_quiet_dashboard(httpx_mock, days=100)
        result = await get_dashboard()

    assert [repo_entry["repo"] for repo_entry in result["repos"]] == ["owner/repo", "owner/other"]
    assert sum(result["summary"]["groups"].values()) == result["summary"]["total"] == 2
    placed = {
        (entry["repo"], entry["id"]): name
        for name, group in result["groups"].items()
        for entry in group
    }
    assert placed == {
        ("owner/repo", "001"): "never_linked",
        ("owner/other", "001"): "stalled",
    }


async def test_get_dashboard_status_filter_narrows_the_groups(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """Filtering to one state groups only that state's requirements."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        _mock_cancelled_dashboard(httpx_mock)
        result = await get_dashboard(status="cancelled")

    assert [entry["id"] for entry in result["groups"]["dropped"]] == ["002"]
    assert sum(result["summary"]["groups"].values()) == 1


async def test_get_dashboard_groups_a_pr_branch_requirement_as_awaiting_merge(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A requirement read off an open PR branch is reported as waiting on that merge."""
    pr_branch = "lachesis/req-002-new-feature"
    pr_md = _SAMPLE_MD.replace('id: "001"', 'id: "002"')
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=[])  # list_files on main → nothing landed yet
        httpx_mock.add_response(
            json=[{"head": {"ref": pr_branch}, "html_url": "https://x/pull/7", "number": 7}]
        )
        pr_file = {"name": "002-new-feature.md", "path": "requirements/002-new-feature.md"}
        httpx_mock.add_response(json=[pr_file])
        httpx_mock.add_response(
            json={
                "name": pr_file["name"],
                "path": pr_file["path"],
                "content": base64.b64encode(pr_md.encode()).decode("ascii"),
                "sha": "sha2",
                "type": "file",
            }
        )
        result = await get_dashboard()

    assert result["groups"]["needs_attention"] == []
    assert [entry["id"] for entry in result["groups"]["awaiting_merge"]] == ["002"]
    assert result["groups"]["awaiting_merge"][0]["pr"]["number"] == 7
    assert result["summary"]["awaiting_merge_count"] == 1
    assert result["summary"]["groups"]["awaiting_merge"] == 1
    assert result["summary"]["incomplete_repos"] == []


async def test_get_dashboard_groups_a_pending_edit_of_a_landed_requirement_as_awaiting_merge(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """An edit awaiting merge on the requirement's own PR is grouped by that PR, not main's copy.

    Grouping keys off the copy the pull request carries: the cancellation it holds
    drops the requirement, where main's still-planned copy would have reported it
    as never linked.
    """
    pr_branch = "lachesis/req-001-user-auth-20260914T100000Z"
    cancelled_md = _SAMPLE_MD.replace('status: "planned"', 'status: "cancelled"\nreason: "Moot"')
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(
            json=[{"head": {"ref": pr_branch}, "html_url": "https://x/pull/7", "number": 7}]
        )
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(cancelled_md, "branchsha"))
        result = await get_dashboard(include_inactive=True)

    reqs = result["repos"][0]["requirements"]
    assert [(req["id"], req["status"], req["pr"]["number"]) for req in reqs] == [
        ("001", "cancelled", 7)
    ]
    assert result["summary"]["total"] == 1
    assert result["summary"]["by_status"] == {"cancelled": 1}
    assert [entry["id"] for entry in result["groups"]["dropped"]] == ["001"]
    assert result["groups"]["never_linked"] == []


async def test_get_dashboard_awaiting_merge_leads_with_the_oldest_pr(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """The PR's open date reaches the group through _pr_field, and orders it.

    The older PR carries the higher id, so the expected order cannot be produced
    by the repo-and-id fallback the entries fall back to without a timestamp.
    """
    older = {
        "head": {"ref": "lachesis/req-003-older"},
        "html_url": "https://x/pull/3",
        "number": 3,
        "created_at": "2026-07-01T00:00:00Z",
    }
    newer = {
        "head": {"ref": "lachesis/req-002-newer"},
        "html_url": "https://x/pull/9",
        "number": 9,
        "created_at": "2026-09-05T00:00:00Z",
    }
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=[])  # list_files on main → nothing landed yet
        httpx_mock.add_response(json=[older, newer])
        for req_id, slug in (("003", "older"), ("002", "newer")):
            pr_file = {"name": f"{req_id}-{slug}.md", "path": f"requirements/{req_id}-{slug}.md"}
            httpx_mock.add_response(json=[pr_file])
            httpx_mock.add_response(
                json={
                    "name": pr_file["name"],
                    "path": pr_file["path"],
                    "content": base64.b64encode(
                        _SAMPLE_MD.replace('id: "001"', f'id: "{req_id}"').encode()
                    ).decode("ascii"),
                    "sha": f"sha-{req_id}",
                    "type": "file",
                }
            )
        result = await get_dashboard()

    awaiting = result["groups"]["awaiting_merge"]
    assert [entry["id"] for entry in awaiting] == ["003", "002"]
    assert awaiting[0]["pr"]["created_at"] == "2026-07-01T00:00:00Z"


async def test_get_dashboard_names_the_repo_whose_pr_fetch_failed(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A failed PR fetch costs the PR branches, not the repo — and says so."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(status_code=403, json={"message": "Forbidden"})
        result = await get_dashboard()

    assert [req["id"] for req in result["repos"][0]["requirements"]] == ["001"]
    summary = result["summary"]
    assert summary["incomplete_repos"] == ["owner/repo"]
    assert summary["awaiting_merge_count"] == 0
    assert summary["total"] == 1
    assert summary["by_status"] == {"planned": 1}


async def test_get_dashboard_keeps_a_repo_whose_pr_branch_read_failed(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A PR branch that could not be read costs that branch, not the repo's whole listing."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(
            json=[
                {
                    "head": {"ref": "lachesis/req-002-new-feature"},
                    "html_url": "https://x/pull/7",
                    "number": 7,
                    "created_at": "2026-09-01T00:00:00Z",
                }
            ]
        )
        httpx_mock.add_response(status_code=403, json={"message": "Forbidden"})
        result = await get_dashboard()

    assert [req["id"] for req in result["repos"][0]["requirements"]] == ["001"]
    assert result["summary"]["incomplete_repos"] == ["owner/repo"]
    assert result["summary"]["total"] == 1


async def test_get_dashboard_counts_awaiting_merge_without_redefining_unlinked(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """The two counts overlap: an unlinked requirement on a PR is counted by both."""
    pr_branch = "lachesis/req-002-new-feature"
    pr_md = _SAMPLE_MD.replace('id: "001"', 'id: "002"')
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(
            json=[{"head": {"ref": pr_branch}, "html_url": "https://x/pull/7", "number": 7}]
        )
        pr_file = {"name": "002-new-feature.md", "path": "requirements/002-new-feature.md"}
        httpx_mock.add_response(json=[pr_file])
        httpx_mock.add_response(
            json={
                "name": pr_file["name"],
                "path": pr_file["path"],
                "content": base64.b64encode(pr_md.encode()).decode("ascii"),
                "sha": "sha2",
                "type": "file",
            }
        )
        result = await get_dashboard()

    summary = result["summary"]
    assert summary["awaiting_merge_count"] == summary["groups"]["awaiting_merge"] == 1
    assert summary["unlinked_count"] == 2


# ---------------------------------------------------------------------------
# landing notifications
# ---------------------------------------------------------------------------


async def test_list_requirements_offers_only_default_branch_landing_candidates(
    httpx_mock: HTTPXMock,
) -> None:
    """A requirement still on a PR branch has not settled and is not a candidate."""
    pr_branch = "lachesis/req-002-new-feature"
    pr_md = _SAMPLE_MD.replace('id: "001"', 'id: "002"')
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(
        json=[{"head": {"ref": pr_branch}, "html_url": "https://x/pull/7", "number": 7}]
    )
    pr_file = {"name": "002-new-feature.md", "path": "requirements/002-new-feature.md"}
    httpx_mock.add_response(json=[pr_file])
    httpx_mock.add_response(
        json={
            "name": pr_file["name"],
            "path": pr_file["path"],
            "content": base64.b64encode(pr_md.encode()).decode("ascii"),
            "sha": "sha2",
            "type": "file",
        }
    )

    with patch("tools.requirements.landing.notify_landed", new_callable=AsyncMock) as notify_landed:
        await list_requirements("owner/repo")

    repo, candidates = notify_landed.await_args.args
    assert repo == "owner/repo"
    assert [c.entry["id"] for c in candidates] == ["001"]
    assert candidates[0].path == _PATH
    assert candidates[0].content == _SAMPLE_MD


async def test_get_dashboard_keeps_the_repo_when_landing_evaluation_raises(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A notification is never worth the listing the caller actually asked for."""
    with (
        patch(
            "tools.requirements.db.list_repos",
            return_value=[{"repo_name": "owner/repo"}],
        ),
        patch(
            "tools.requirements.landing.notify_landed",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        result = await get_dashboard()

    assert [r["id"] for r in result["repos"][0]["requirements"]] == ["001"]


async def test_get_dashboard_offers_candidates_that_reached_derivation(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A cancelled requirement never reaches _attach_derived, so it is not a candidate."""
    with (
        patch(
            "tools.requirements.db.list_repos",
            return_value=[{"repo_name": "owner/repo"}],
        ),
        patch("tools.requirements.landing.notify_landed", new_callable=AsyncMock) as notify_landed,
    ):
        _mock_cancelled_dashboard(httpx_mock)
        await get_dashboard()

    candidates = notify_landed.await_args.args[1]
    assert [c.entry["id"] for c in candidates] == ["001"]
    assert all("derived" in c.entry for c in candidates)


# ---------------------------------------------------------------------------
# PR-branch files already read from the default branch
# ---------------------------------------------------------------------------

_THIRD_PATH = "requirements/003-audit-log.md"
_THIRD_MD = _SAMPLE_MD.replace('id: "001"', 'id: "003"')
_SECOND_SAMPLE_MD = _SAMPLE_MD.replace('id: "001"', 'id: "002"')


def _make_third_file_response(content: str, sha: str) -> dict:
    """Build a Contents API file object for a requirement only a PR branch has."""
    return {
        "name": "003-audit-log.md",
        "path": _THIRD_PATH,
        "content": base64.b64encode(content.encode()).decode("ascii"),
        "sha": sha,
        "type": "file",
    }


def _register_pr_branch_repeating_main(httpx_mock: HTTPXMock) -> None:
    """Two requirements on the default branch, a PR branch adding a third to them."""
    httpx_mock.add_response(json=_TWO_FILES_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(json=_make_second_file_response(_SECOND_SAMPLE_MD, "sha2"))
    branch = "lachesis/req-003-audit-log"
    httpx_mock.add_response(
        json=[{"head": {"ref": branch}, "html_url": "https://x/pull/9", "number": 9}]
    )
    httpx_mock.add_response(
        json=[*_TWO_FILES_RESP, {"name": "003-audit-log.md", "path": _THIRD_PATH}]
    )
    httpx_mock.add_response(json=_make_third_file_response(_THIRD_MD, "sha3"))


async def test_get_dashboard_does_not_refetch_pr_branch_files_already_on_main(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A PR branch carries the whole default branch; refetching it costs a request each."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        _register_pr_branch_repeating_main(httpx_mock)
        result = await get_dashboard()

    requirements = result["repos"][0]["requirements"]
    assert sorted(req["id"] for req in requirements) == ["001", "002", "003"]
    assert {req["id"] for req in requirements if req["pr"]} == {"003"}
    # Two directory listings, three file reads, one pull-request lookup.
    assert len(httpx_mock.get_requests()) == 6


async def test_list_requirements_does_not_refetch_pr_branch_files_already_on_main(
    httpx_mock: HTTPXMock,
) -> None:
    """The same skip on the other call site that walks PR branches."""
    _register_pr_branch_repeating_main(httpx_mock)

    result = await list_requirements("owner/repo")

    assert sorted(req["id"] for req in result) == ["001", "002", "003"]
    assert {req["id"] for req in result if req["pr"]} == {"003"}
    assert len(httpx_mock.get_requests()) == 6


# ---------------------------------------------------------------------------
# get_dashboard failed_repos
# ---------------------------------------------------------------------------


async def test_get_dashboard_names_a_repo_it_could_not_read(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A repo that contributed nothing is named, not silently subtracted from the totals."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/first"}, {"repo_name": "owner/second"}],
    ):
        httpx_mock.add_response(
            status_code=403,
            headers={"x-ratelimit-remaining": "0"},
            json={"message": "API rate limit exceeded"},
        )
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        result = await get_dashboard()

    summary = result["summary"]
    assert [repo["repo"] for repo in result["repos"]] == ["owner/second"]
    assert summary["failed_repos"] == [{"repo": "owner/first", "cause": "rate_limited"}]
    assert summary["unavailable_repos"] == []
    assert summary["incomplete_repos"] == []


async def test_get_dashboard_reports_no_failed_repos_when_every_repo_was_read(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """The key is present and empty rather than absent when nothing failed."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        result = await get_dashboard()

    assert result["summary"]["failed_repos"] == []


async def test_get_dashboard_calls_an_unclassifiable_repo_failure_transient(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A file with no id, in frontmatter or name, fails no HTTP check, so only the catch-all
    can name it.

    It fails on the second file, which also pins how far the counts are a floor:
    ``total`` keeps what was tallied before the failure, the listings keep none of it.
    """
    unnumbered = {"name": "data-export.md", "path": "requirements/data-export.md"}
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=[{"name": _NAME, "path": _PATH}, unnumbered])
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(
            json={
                **unnumbered,
                "content": base64.b64encode(_SAMPLE_MD.replace('id: "001"\n', "").encode()).decode(
                    "ascii"
                ),
                "sha": "sha2",
                "type": "file",
            }
        )
        httpx_mock.add_response(json=[])  # list_lachesis_prs → the files are parsed after it
        result = await get_dashboard()

    assert result["summary"]["failed_repos"] == [{"repo": "owner/repo", "cause": "transient"}]
    assert result["repos"] == []
    assert result["summary"]["total"] == 1


async def test_get_dashboard_does_not_call_a_listed_repo_failed(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """A raise after the listing already landed costs a log line, not the repo's name."""
    with (
        patch(
            "tools.requirements.db.list_repos",
            return_value=[{"repo_name": "owner/repo"}],
        ),
        patch(
            "tools.requirements.landing.notify_landed",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
    ):
        httpx_mock.add_response(json=_LIST_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        result = await get_dashboard()

    assert [req["id"] for req in result["repos"][0]["requirements"]] == ["001"]
    assert result["summary"]["failed_repos"] == []


# ---------------------------------------------------------------------------
# Unquoted zero-padded ids — YAML 1.1 reads "id: 020" as the octal int 16
# ---------------------------------------------------------------------------

_OCTAL_LOOKING_NAME = "020-self-registration.md"
_OCTAL_LOOKING_PATH = f"requirements/{_OCTAL_LOOKING_NAME}"
_OCTAL_LOOKING_MD = _SAMPLE_MD.replace('id: "001"', "id: 020").replace(
    "User authentication", "Self registration"
)


def _octal_looking_file_response(sha: str = "sha2") -> dict:
    return {
        "name": _OCTAL_LOOKING_NAME,
        "path": _OCTAL_LOOKING_PATH,
        "content": base64.b64encode(_OCTAL_LOOKING_MD.encode()).decode("ascii"),
        "sha": sha,
        "type": "file",
    }


_WITH_OCTAL_LOOKING_RESP = [
    {"name": _NAME, "path": _PATH},
    {"name": _OCTAL_LOOKING_NAME, "path": _OCTAL_LOOKING_PATH},
]


async def test_list_requirements_reads_an_unquoted_zero_padded_id_as_written(
    httpx_mock: HTTPXMock,
) -> None:
    """An unquoted 'id: 020' is listed as "020", the file's own prefix, not as 16."""
    httpx_mock.add_response(json=_WITH_OCTAL_LOOKING_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(json=_octal_looking_file_response())
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs

    result = await list_requirements("owner/repo")

    assert [req["id"] for req in result] == ["001", "020"]


async def test_get_dashboard_succeeds_unfiltered_beside_an_unquoted_zero_padded_id(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """Sorting an unquoted 'id: 020' among string ids does not take the dashboard down."""
    with patch(
        "tools.requirements.db.list_repos",
        return_value=[{"repo_name": "owner/repo"}],
    ):
        httpx_mock.add_response(json=_WITH_OCTAL_LOOKING_RESP)
        httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
        httpx_mock.add_response(json=_octal_looking_file_response())
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        result = await get_dashboard()

    assert [req["id"] for req in result["repos"][0]["requirements"]] == ["001", "020"]
    assert [req["id"] for req in result["groups"]["never_linked"]] == ["001", "020"]


async def test_update_requirement_rewrites_an_unquoted_zero_padded_id_quoted(
    httpx_mock: HTTPXMock,
) -> None:
    """The next write repairs 'id: 020' instead of preserving it as 16."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=[{"name": _OCTAL_LOOKING_NAME, "path": _OCTAL_LOOKING_PATH}])
    httpx_mock.add_response(json=_octal_looking_file_response())
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await update_requirement("owner/repo", "020", {"title": "Renamed"})

    assert "id: '020'" in result["content"]
    assert frontmatter.loads(result["content"])["id"] == "020"


async def test_link_issues_rewrites_an_unquoted_zero_padded_id_quoted(
    httpx_mock: HTTPXMock,
) -> None:
    """Linking issues repairs 'id: 020' instead of preserving it as 16."""
    httpx_mock.add_response(json=[])  # find_open_pr → no open PRs
    httpx_mock.add_response(json=[{"name": _OCTAL_LOOKING_NAME, "path": _OCTAL_LOOKING_PATH}])
    httpx_mock.add_response(json=_octal_looking_file_response())
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await link_issues("owner/repo", "020", _ISSUES)

    assert "id: '020'" in result["content"]
    assert frontmatter.loads(result["content"])["id"] == "020"


async def test_create_requirement_writes_an_id_yaml_would_not_quote_quoted(
    httpx_mock: HTTPXMock,
) -> None:
    """'008' is not octal, so YAML leaves it bare unless told; a bare id parses inconsistently."""
    httpx_mock.add_response(json=[{"name": "007-deploy.md", "path": "requirements/007-deploy.md"}])
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no open PRs
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    await create_requirement("owner/repo", "New Feature", "Why", "What")

    body = json.loads(httpx_mock.get_requests()[2].content)
    assert "id: '008'" in base64.b64decode(body["content"]).decode()
