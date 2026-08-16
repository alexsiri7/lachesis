"""Unit tests for mcp/tools/requirements.py — link_issues."""

from __future__ import annotations

import base64
import json
from datetime import date
from unittest.mock import patch

import frontmatter
import httpx
import pytest
from pytest_httpx import HTTPXMock

from auth import current_db_user_id
from tools.requirements import (
    create_github_issues,
    create_requirement,
    delete_requirement,
    get_dashboard,
    get_requirement,
    link_issues,
    list_requirements,
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
        }
    ]


# ---------------------------------------------------------------------------
# create_requirement tests
# ---------------------------------------------------------------------------


async def test_create_requirement_returns_id_and_path(httpx_mock: HTTPXMock) -> None:
    """Returns id and path for the created requirement."""
    httpx_mock.add_response(json=_LIST_RESP)  # list_files → existing [001-user-auth.md]
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file

    result = await create_requirement("owner/repo", "New Feature", "Because X", "Do Y")

    assert result["id"] == "002"
    assert result["path"] == "requirements/002-new-feature.md"


async def test_create_requirement_empty_repo_assigns_001(httpx_mock: HTTPXMock) -> None:
    """When requirements/ is empty or absent, first ID is 001."""
    httpx_mock.add_response(json=[])  # list_files → no files
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await create_requirement("owner/repo", "First req", "Why", "What")

    assert result["id"] == "001"
    assert result["path"].startswith("requirements/001-")


async def test_create_requirement_slug_derived_from_title(httpx_mock: HTTPXMock) -> None:
    """Slug lowercases and hyphenates title."""
    httpx_mock.add_response(json=[])
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await create_requirement("owner/repo", "Hello World!", "Why", "What")

    assert "hello-world" in result["path"]


async def test_create_requirement_sends_draft_status(httpx_mock: HTTPXMock) -> None:
    """Created file has status 'draft' and github_issue null."""
    httpx_mock.add_response(json=[])
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    await create_requirement("owner/repo", "Test req", "Why text", "What text")

    create_request = httpx_mock.get_requests()[1]
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
    httpx_mock.add_response(json=existing)
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    result = await create_requirement("owner/repo", "New Feature", "Why", "What")

    assert result["id"] == "006"
    assert result["path"] == "requirements/006-new-feature.md"


async def test_create_requirement_falls_back_to_branch_pr_on_409(httpx_mock: HTTPXMock) -> None:
    """On 409 from create_file, creates branch and PR, returns pr_url."""
    httpx_mock.add_response(json=[])  # list_files → empty
    httpx_mock.add_response(
        status_code=409, json={"message": "Protected branch"}
    )  # create_file → 409  # noqa: E501
    httpx_mock.add_response(json={"object": {"sha": "deadbeef"}})  # get_branch_sha
    httpx_mock.add_response(status_code=201, json={})  # create_branch
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file on branch
    httpx_mock.add_response(
        status_code=201,
        json={"number": 5, "html_url": "https://github.com/owner/repo/pull/5"},
    )  # create_pull_request

    result = await create_requirement("owner/repo", "My Feature", "Why", "What")

    assert result["id"] == "001"
    assert result["path"] == "requirements/001-my-feature.md"
    assert result["pr_url"] == "https://github.com/owner/repo/pull/5"
    # request order: [0]=list_files, [1]=create_file(409),
    #                [2]=get_branch_sha, [3]=create_branch, [4]=create_file(branch), [5]=create_pr
    branch_create_file_req = httpx_mock.get_requests()[4]
    body = json.loads(branch_create_file_req.content)
    assert body["branch"] == "lachesis/req-001-my-feature"


async def test_create_requirement_non_409_error_propagates(httpx_mock: HTTPXMock) -> None:
    """Non-409 errors from create_file must propagate, not trigger the branch fallback."""
    httpx_mock.add_response(json=[])  # list_files
    httpx_mock.add_response(status_code=403, json={"message": "Forbidden"})  # create_file → 403

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await create_requirement("owner/repo", "My Feature", "Why", "What")
    assert exc_info.value.response.status_code == 403
    # Only 2 requests made — no branch/PR calls
    assert len(httpx_mock.get_requests()) == 2


async def test_create_requirement_no_pr_url_on_direct_write(httpx_mock: HTTPXMock) -> None:
    """When create_file succeeds directly, pr_url is absent from response."""
    httpx_mock.add_response(json=[])  # list_files
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file success

    result = await create_requirement("owner/repo", "Direct Write", "Why", "What")

    assert "pr_url" not in result


async def test_create_requirement_409_uses_correct_branch_name(httpx_mock: HTTPXMock) -> None:
    """Branch name follows lachesis/req-{id}-{slug} pattern."""
    httpx_mock.add_response(json=[])  # list_files
    httpx_mock.add_response(status_code=409, json={"message": "Protected branch"})
    httpx_mock.add_response(json={"object": {"sha": "abc"}})  # get_branch_sha
    httpx_mock.add_response(status_code=201, json={})  # create_branch — capture this
    httpx_mock.add_response(json={"content": {}, "commit": {}})  # create_file on branch
    httpx_mock.add_response(
        status_code=201, json={"number": 1, "html_url": "http://github.com/pr/1"}
    )

    await create_requirement("owner/repo", "Hello World", "Why", "What")

    # 3rd request (index 2) is get_branch_sha, 4th (index 3) is create_branch
    create_branch_req = httpx_mock.get_requests()[3]
    body = json.loads(create_branch_req.content)
    assert body["ref"] == "refs/heads/lachesis/req-001-hello-world"


# ---------------------------------------------------------------------------
# list_requirements tests
# ---------------------------------------------------------------------------


async def test_list_requirements_returns_metadata(httpx_mock: HTTPXMock) -> None:
    """Returns list of dicts with id, title, status, github_issue."""
    httpx_mock.add_response(json=_LIST_RESP)  # list_files
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))  # get_file

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

    result = await list_requirements("owner/repo")

    assert result == []
    assert len(httpx_mock.get_requests()) == 1  # only list_files, no get_file


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

    with patch("tools.requirements.db.list_repos", return_value=registered):
        result = await list_requirements("lachesis")

    assert len(result) == 1
