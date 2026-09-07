"""Unit tests for mcp/tools/requirements.py — link_issues."""

from __future__ import annotations

import base64
import contextlib
import json
from datetime import date
from unittest.mock import MagicMock, patch

import frontmatter
import httpx
import pytest
from pytest_httpx import HTTPXMock

from auth import current_db_user_id
from tools.requirements import (
    add_dependency,
    create_github_issues,
    create_requirement,
    delete_requirement,
    get_backlog,
    get_dashboard,
    get_dependencies,
    get_github_issue,
    get_initial_instructions,
    get_requirement,
    link_issues,
    list_requirements,
    move_issue,
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


def _mock_full_flow(httpx_mock: HTTPXMock, content: str = _SAMPLE_MD, sha: str = "sha1") -> None:
    """Register list_files → get_file → update_file responses."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(content, sha))
    httpx_mock.add_response(json={"content": {}, "commit": {}})


async def test_link_issues_updates_frontmatter(
    httpx_mock: HTTPXMock,
) -> None:
    """github_issue is set to the first issue's number."""
    _mock_full_flow(httpx_mock)

    result = await link_issues("owner/repo", "001", _ISSUES)

    post = frontmatter.loads(result)
    assert post["github_issue"] == 42
    assert post["updated"] == date.today().isoformat()


async def test_link_issues_replaces_issues_section(
    httpx_mock: HTTPXMock,
) -> None:
    """## Issues section is replaced with a formatted bullet list."""
    _mock_full_flow(httpx_mock)

    result = await link_issues("owner/repo", "001", _ISSUES)

    assert "- #42 \u2014 Set up JWT middleware" in result
    assert "- #43 \u2014 Build login endpoints" in result


async def test_link_issues_sends_correct_sha_to_update(
    httpx_mock: HTTPXMock,
) -> None:
    """update_file is called with the sha returned by get_file."""
    _mock_full_flow(httpx_mock, sha="abc999")

    await link_issues("owner/repo", "001", _ISSUES)

    update_request = httpx_mock.get_requests()[2]
    body = json.loads(update_request.content)
    assert body["sha"] == "abc999"


async def test_link_issues_raises_when_requirement_not_found(
    httpx_mock: HTTPXMock,
) -> None:
    """FileNotFoundError if no file in requirements/ starts with the id."""
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])
    httpx_mock.add_response(json=[])  # find_pr_branch → no open PRs

    with pytest.raises(FileNotFoundError, match="001"):
        await link_issues("owner/repo", "001", _ISSUES)


async def test_link_issues_found_on_pr_branch_writes_back_to_branch(
    httpx_mock: HTTPXMock,
) -> None:
    """When requirement lives on a PR branch, link_issues reads from and writes to that branch."""
    pr_branch = "lachesis/req-001-user-auth"
    # 1. list_files on default branch → empty (req not on main)
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])
    # 2. find_pr_branch → GET /pulls?state=open returns a PR with matching head
    httpx_mock.add_response(json=[{"head": {"ref": pr_branch}}])
    # 3. list_files on PR branch → returns the requirement file
    httpx_mock.add_response(json=_LIST_RESP)
    # 4. get_file on PR branch
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "branchsha"))
    # 5. update_file
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await link_issues("owner/repo", "001", _ISSUES)

    # Content was correctly updated
    post = frontmatter.loads(result)
    assert post["github_issue"] == 42

    # update_file request must include branch in payload
    requests = httpx_mock.get_requests()
    update_request = requests[4]
    body = json.loads(update_request.content)
    assert body["branch"] == pr_branch
    assert body["sha"] == "branchsha"

    # list_files (request 2) and get_file (request 3) must use PR branch ref
    # Branch name is URL-encoded in the query string (slashes → %2F)
    pr_branch_encoded = pr_branch.replace("/", "%2F")
    assert f"ref={pr_branch_encoded}" in str(requests[2].url)
    assert f"ref={pr_branch_encoded}" in str(requests[3].url)


async def test_link_issues_raises_when_not_found_on_main_or_pr_branch(
    httpx_mock: HTTPXMock,
) -> None:
    """FileNotFoundError when file is absent from default branch and no matching open PR."""
    # list_files on default branch → no match
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])
    # find_pr_branch → open PRs exist but none match lachesis/req-001-*
    httpx_mock.add_response(json=[{"head": {"ref": "lachesis/req-002-something-else"}}])

    with pytest.raises(FileNotFoundError, match="001"):
        await link_issues("owner/repo", "001", _ISSUES)


async def test_link_issues_empty_issues_list(
    httpx_mock: HTTPXMock,
) -> None:
    """Empty issues list clears ## Issues section."""
    _mock_full_flow(httpx_mock)

    result = await link_issues("owner/repo", "001", [])

    post = frontmatter.loads(result)
    assert post.get("github_issue") is None
    assert "## Issues" in result
    # No bullet lines
    lines = result.splitlines()
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

    assert "## Issues" in result
    assert "- #42 \u2014 Set up JWT middleware" in result


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
    """Register list_files \u2192 get_file \u2192 delete_file responses."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(content, sha))
    httpx_mock.add_response(status_code=200, json={"commit": {}, "content": None})


async def test_delete_requirement_returns_deleted_path(
    httpx_mock: HTTPXMock,
) -> None:
    """Returns {"deleted": path} on successful deletion."""
    _mock_delete_flow(httpx_mock)

    result = await delete_requirement("owner/repo", "001")

    assert result == {"deleted": _PATH}


async def test_delete_requirement_refuses_done_status(
    httpx_mock: HTTPXMock,
) -> None:
    """Raises ValueError with explanation when status is 'done'."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_DONE_MD, "sha1"))

    with pytest.raises(ValueError, match="done requirement"):
        await delete_requirement("owner/repo", "001")


async def test_delete_requirement_raises_when_not_found(
    httpx_mock: HTTPXMock,
) -> None:
    """FileNotFoundError if no file starts with the id prefix."""
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])
    httpx_mock.add_response(json=[])  # find_pr_branch → no PRs

    with pytest.raises(FileNotFoundError, match="001"):
        await delete_requirement("owner/repo", "001")


async def test_delete_requirement_sends_correct_sha(
    httpx_mock: HTTPXMock,
) -> None:
    """delete_file is called with the sha returned by get_file."""
    import json as _json

    _mock_delete_flow(httpx_mock, sha="deadbeef")

    await delete_requirement("owner/repo", "001")

    delete_request = httpx_mock.get_requests()[2]
    body = _json.loads(delete_request.content)
    assert body["sha"] == "deadbeef"


async def test_delete_requirement_sends_correct_commit_message(
    httpx_mock: HTTPXMock,
) -> None:
    """Commit message references the requirement id."""
    import json as _json

    _mock_delete_flow(httpx_mock)

    await delete_requirement("owner/repo", "001")

    delete_request = httpx_mock.get_requests()[2]
    body = _json.loads(delete_request.content)
    assert "001" in body["message"]


# ---------------------------------------------------------------------------
# create_github_issues tests
# ---------------------------------------------------------------------------

_ISSUES_URL = "https://api.github.com/repos/owner/repo/issues"


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
            "ready": False,
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
            "ready": False,
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
            "warning": "no linked issues",
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

    result = await list_requirements("owner/repo")

    assert len(result) == 2
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
    httpx_mock.add_response(json=_LIST_RESP)  # list_files
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))  # get_file

    result = await get_requirement("owner/repo", "001")

    assert result == _SAMPLE_MD


async def test_get_requirement_raises_when_not_found(httpx_mock: HTTPXMock) -> None:
    """FileNotFoundError if no file starts with the id prefix."""
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs

    with pytest.raises(FileNotFoundError, match="001"):
        await get_requirement("owner/repo", "001")


async def test_get_requirement_matches_by_id_prefix(httpx_mock: HTTPXMock) -> None:
    """Correctly finds a file by its numeric id prefix."""
    second_md = "---\nid: '002'\ntitle: Other\nstatus: idea\n---\nBody\n"
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

    assert result == second_md


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

    result = await list_requirements("owner/repo")

    assert result[0]["github_issue"] == 42
    assert result[0]["warning"] is None


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
        result = await get_dashboard()

    assert result["summary"]["unlinked_count"] == 0


# ---------------------------------------------------------------------------
# update_requirement — draft status tests
# ---------------------------------------------------------------------------


async def test_update_requirement_accepts_draft_status(httpx_mock: HTTPXMock) -> None:
    """'draft' is accepted as a valid status value."""
    _mock_full_flow(httpx_mock)
    result = await update_requirement("owner/repo", "001", {"status": "draft"})
    post = frontmatter.loads(result)
    assert post["status"] == "draft"


# ---------------------------------------------------------------------------
# link_issues — draft auto-advance tests
# ---------------------------------------------------------------------------


async def test_link_issues_advances_draft_to_idea(httpx_mock: HTTPXMock) -> None:
    """link_issues advances status from 'draft' to 'idea'."""
    draft_md = _SAMPLE_MD.replace('status: "planned"', 'status: "draft"')
    _mock_full_flow(httpx_mock, content=draft_md)
    result = await link_issues("owner/repo", "001", _ISSUES)
    post = frontmatter.loads(result)
    assert post["status"] == "idea"


async def test_link_issues_does_not_change_non_draft_status(httpx_mock: HTTPXMock) -> None:
    """link_issues does not touch status when requirement is not 'draft'."""
    _mock_full_flow(httpx_mock)  # _SAMPLE_MD has status: "planned"
    result = await link_issues("owner/repo", "001", _ISSUES)
    post = frontmatter.loads(result)
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
    """Requirements on open PR branches appear in list with pr_url field."""
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
    assert "pr_url" not in result[0]  # main-branch req has no pr_url
    assert result[1]["id"] == "002"
    assert result[1]["pr_url"] == pr_url
    assert result[1]["branch"] == pr_branch
    assert result[1]["pr_number"] == 7
    assert result[1]["warning"] == "no linked issues"


async def test_list_requirements_deduplicates_pr_and_main(httpx_mock: HTTPXMock) -> None:
    """If the same requirement ID exists on main and a PR branch, main wins (no pr_url)."""
    pr_branch = "lachesis/req-001-user-auth"
    # 1. main → returns 001
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    # 2. list_lachesis_prs → PR exists for same req
    pr_url_3 = "https://github.com/owner/repo/pull/3"
    httpx_mock.add_response(json=[{"head": {"ref": pr_branch}, "html_url": pr_url_3, "number": 3}])
    # 3. list_files on PR branch → also has 001
    httpx_mock.add_response(json=_LIST_RESP)
    # 4. get_file on PR branch (needed to read frontmatter id for dedup check)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha2"))

    result = await list_requirements("owner/repo")

    assert len(result) == 1
    assert result[0]["id"] == "001"
    assert "pr_url" not in result[0]
    assert "branch" not in result[0]
    assert "pr_number" not in result[0]


async def test_list_requirements_no_prs_returns_main_only(httpx_mock: HTTPXMock) -> None:
    """When no open Lachesis PRs exist, result is same as before."""
    httpx_mock.add_response(json=_LIST_RESP)
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → empty

    result = await list_requirements("owner/repo")

    assert len(result) == 1
    assert result[0]["id"] == "001"


# ---------------------------------------------------------------------------
# get_requirement — PR branch support
# ---------------------------------------------------------------------------


async def test_get_requirement_finds_on_pr_branch(httpx_mock: HTTPXMock) -> None:
    """If not on main, get_requirement finds the file on an open PR branch."""
    pr_branch = "lachesis/req-001-user-auth"
    # 1. list_files on main → no match (002 only)
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])
    # 2. list_lachesis_prs → GET /pulls?state=open
    httpx_mock.add_response(
        json=[{"head": {"ref": pr_branch}, "html_url": "https://github.com/owner/repo/pull/1"}]
    )
    # 3. list_files on PR branch → has 001
    httpx_mock.add_response(json=_LIST_RESP)
    # 4. get_file on PR branch
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))

    result = await get_requirement("owner/repo", "001")

    assert result == _SAMPLE_MD
    # Verify branch ref used in list_files and get_file calls
    requests = httpx_mock.get_requests()
    pr_branch_encoded = pr_branch.replace("/", "%2F")
    assert f"ref={pr_branch_encoded}" in str(requests[2].url)
    assert f"ref={pr_branch_encoded}" in str(requests[3].url)


async def test_get_requirement_raises_when_not_on_main_or_any_pr(httpx_mock: HTTPXMock) -> None:
    """FileNotFoundError when not on main and no PR branch has the file."""
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs

    with pytest.raises(FileNotFoundError, match="001"):
        await get_requirement("owner/repo", "001")


# ---------------------------------------------------------------------------
# update_requirement — PR branch support
# ---------------------------------------------------------------------------


async def test_update_requirement_writes_to_pr_branch(httpx_mock: HTTPXMock) -> None:
    """update_requirement reads from and writes to the PR branch when req is not on main."""
    pr_branch = "lachesis/req-001-user-auth"
    # 1. list_files on main → no match
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])
    # 2. find_pr_branch → GET /pulls?state=open
    httpx_mock.add_response(json=[{"head": {"ref": pr_branch}}])
    # 3. list_files on PR branch
    httpx_mock.add_response(json=_LIST_RESP)
    # 4. get_file on PR branch
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "branchsha"))
    # 5. update_file
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await update_requirement("owner/repo", "001", {"status": "planned"})

    post = frontmatter.loads(result)
    assert post["status"] == "planned"

    requests = httpx_mock.get_requests()
    update_req = requests[4]
    body = json.loads(update_req.content)
    assert body["branch"] == pr_branch
    assert body["sha"] == "branchsha"


async def test_update_requirement_raises_when_not_found_on_main_or_pr(
    httpx_mock: HTTPXMock,
) -> None:
    """FileNotFoundError when not on main and no matching PR branch."""
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])
    httpx_mock.add_response(json=[])  # find_pr_branch → no PRs

    with pytest.raises(FileNotFoundError, match="001"):
        await update_requirement("owner/repo", "001", {"status": "planned"})


# ---------------------------------------------------------------------------
# delete_requirement — PR branch support
# ---------------------------------------------------------------------------


async def test_delete_requirement_deletes_from_pr_branch(httpx_mock: HTTPXMock) -> None:
    """delete_requirement deletes from the PR branch when req is not on main."""
    pr_branch = "lachesis/req-001-user-auth"
    # 1. list_files on main → no match
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])
    # 2. find_pr_branch → GET /pulls?state=open
    httpx_mock.add_response(json=[{"head": {"ref": pr_branch}}])
    # 3. list_files on PR branch
    httpx_mock.add_response(json=_LIST_RESP)
    # 4. get_file on PR branch
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "branchsha"))
    # 5. delete_file
    httpx_mock.add_response(status_code=200, json={"commit": {}, "content": None})

    result = await delete_requirement("owner/repo", "001")

    assert result == {"deleted": _PATH}
    requests = httpx_mock.get_requests()
    delete_req = requests[4]
    body = json.loads(delete_req.content)
    assert body["branch"] == pr_branch
    assert body["sha"] == "branchsha"


async def test_delete_requirement_raises_when_not_found_on_main_or_pr(
    httpx_mock: HTTPXMock,
) -> None:
    """FileNotFoundError when not on main and no matching PR branch."""
    httpx_mock.add_response(json=[{"name": "002-other.md", "path": "requirements/002-other.md"}])
    httpx_mock.add_response(json=[])  # find_pr_branch → no PRs

    with pytest.raises(FileNotFoundError, match="001"):
        await delete_requirement("owner/repo", "001")


# ---------------------------------------------------------------------------
# get_dashboard — PR branch support
# ---------------------------------------------------------------------------


async def test_get_dashboard_includes_pr_branch_requirements(
    httpx_mock: HTTPXMock, set_db_user_id: None
) -> None:
    """Dashboard includes requirements from open PR branches with pr_url field."""
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
    assert "pr_url" not in main_req
    assert pr_req["pr_url"] == pr_url
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


async def test_get_initial_instructions_documents_the_ready_gate() -> None:
    """Archon reads these at session start, so the pickup gate has to be discoverable."""
    result = await get_initial_instructions()

    assert "set_backlog_flags" in result
    assert "move_issue" in result


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
                "ready": False,
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
                "ready": False,
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


def _mock_open_issues(httpx_mock: HTTPXMock, numbers: list[int]) -> None:
    httpx_mock.add_response(
        url=_OPEN_ISSUES_URL,
        json=[{"number": n, "title": f"Issue {n}"} for n in numbers],
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


async def test_get_backlog_defaults_new_issues_to_unready(
    httpx_mock: HTTPXMock, set_user_id: str
) -> None:
    """An issue with no row is unready and not an interrupt."""
    _mock_open_issues(httpx_mock, [1])

    with _patch_backlog(_FakeBacklog()):
        result = await get_backlog("owner/repo")

    assert result == [
        {"number": 1, "title": "Issue 1", "position": 0, "ready": False, "interrupt": False}
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
    """The default path is unready and last, which needs no row and so no DB call."""
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
    assert result[0]["ready"] is False
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
