"""Unit tests for mcp/tools/requirements.py — link_issues."""

from __future__ import annotations

import base64
import json
from datetime import date

import frontmatter
import httpx
import pytest
from pytest_httpx import HTTPXMock

from tools.requirements import (
    create_github_issues,
    create_requirement,
    delete_requirement,
    get_requirement,
    link_issues,
    list_requirements,
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
        }
    ]


async def test_create_github_issues_multiple(httpx_mock: HTTPXMock) -> None:
    """Multiple issues are created sequentially."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={"id": 200, "number": 1, "title": "First", "html_url": "https://github.com/owner/repo/issues/1"},
    )
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={"id": 201, "number": 2, "title": "Second", "html_url": "https://github.com/owner/repo/issues/2"},
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
        json={"id": 1001, "number": 1, "title": "A", "html_url": "https://github.com/owner/repo/issues/1"},
    )
    # Issue B created second
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={"id": 1002, "number": 2, "title": "B", "html_url": "https://github.com/owner/repo/issues/2"},
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
        json={"id": 2000, "number": 5, "title": "New", "html_url": "https://github.com/owner/repo/issues/5"},
    )
    # GET of pre-existing issue #3
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/owner/repo/issues/3",
        json={"id": 999, "number": 3, "title": "Old"},
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


async def test_create_github_issues_external_blocker_not_found_raises(
    httpx_mock: HTTPXMock,
) -> None:
    """If the pre-existing blocker GET returns 404, HTTPStatusError propagates."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={"id": 3000, "number": 10, "title": "New", "html_url": "https://github.com/owner/repo/issues/10"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/owner/repo/issues/99",
        status_code=404,
        json={"message": "Not Found"},
    )

    with pytest.raises(httpx.HTTPStatusError):
        await create_github_issues(
            "owner/repo",
            [{"title": "New", "body": "Body", "blocked_by": [99]}],
        )


async def test_create_github_issues_multiple_blockers_on_one_issue(httpx_mock: HTTPXMock) -> None:
    """An issue with two blockers (one in-batch, one external) wires both dependencies."""
    # Issue A created first (in-batch blocker)
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={"id": 10, "number": 1, "title": "A", "html_url": "https://github.com/owner/repo/issues/1"},
    )
    # Issue C created second (the blocked issue)
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={"id": 30, "number": 3, "title": "C", "html_url": "https://github.com/owner/repo/issues/3"},
    )
    # Dependency POST for blocker A (in-batch, id=10) — processed before the external GET
    httpx_mock.add_response(
        method="POST",
        url=_DEP_URL_TEMPLATE.format(n=3),
        status_code=200,
        json={},
    )
    # GET external blocker #99
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/owner/repo/issues/99",
        json={"id": 990, "number": 99, "title": "External"},
    )
    # Dependency POST for blocker #99 (external, id=990)
    httpx_mock.add_response(
        method="POST",
        url=_DEP_URL_TEMPLATE.format(n=3),
        status_code=200,
        json={},
    )

    result = await create_github_issues(
        "owner/repo",
        [
            {"title": "A", "body": "Body A"},
            {"title": "C", "body": "Body C", "blocked_by": [1, 99]},
        ],
    )

    assert result[1]["blocked_by"] == [1, 99]
    # Request order: POST A, POST C, POST dep(A→issue3), GET #99, POST dep(99→issue3)
    requests = httpx_mock.get_requests()
    dep_request_a = requests[2]   # POST dep for in-batch blocker A (id=10)
    dep_request_ext = requests[4]  # POST dep for external blocker #99 (id=990)
    assert json.loads(dep_request_a.content) == {"issue_id": 10}
    assert json.loads(dep_request_ext.content) == {"issue_id": 990}


async def test_create_github_issues_no_blocked_by_field_unchanged(httpx_mock: HTTPXMock) -> None:
    """Issues without blocked_by still work and return empty blocked_by list."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={"id": 100, "number": 7, "title": "No dep", "html_url": "https://github.com/owner/repo/issues/7"},
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


async def test_create_requirement_sends_idea_status(httpx_mock: HTTPXMock) -> None:
    """Created file has status 'idea' and github_issue null."""
    httpx_mock.add_response(json=[])
    httpx_mock.add_response(json={"content": {}, "commit": {}})

    await create_requirement("owner/repo", "Test req", "Why text", "What text")

    create_request = httpx_mock.get_requests()[1]
    body = json.loads(create_request.content)
    decoded = base64.b64decode(body["content"]).decode("utf-8")
    post = frontmatter.loads(decoded)
    assert post["status"] == "idea"
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


# ---------------------------------------------------------------------------
# list_requirements tests
# ---------------------------------------------------------------------------


async def test_list_requirements_returns_metadata(httpx_mock: HTTPXMock) -> None:
    """Returns list of dicts with id, title, status, github_issue."""
    httpx_mock.add_response(json=_LIST_RESP)  # list_files
    httpx_mock.add_response(json=_make_file_response(_SAMPLE_MD, "sha1"))  # get_file

    result = await list_requirements("owner/repo")

    assert result == [
        {"id": "001", "title": "User authentication", "status": "planned", "github_issue": None},
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
    assert result[1]["id"] == "002"
    assert result[1]["github_issue"] == 99


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
