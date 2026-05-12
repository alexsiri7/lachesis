"""Unit tests for mcp/tools/requirements.py — link_issues."""

from __future__ import annotations

import base64
import json
from datetime import date

import frontmatter
import pytest
from pytest_httpx import HTTPXMock

from tools.requirements import (
    create_github_issues,
    create_requirement,
    delete_requirement,
    link_issues,
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


def _mock_full_flow(
    httpx_mock: HTTPXMock, content: str = _SAMPLE_MD, sha: str = "sha1"
) -> None:
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
    httpx_mock.add_response(
        json=[{"name": "002-other.md", "path": "requirements/002-other.md"}]
    )

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


def _mock_delete_flow(
    httpx_mock: HTTPXMock, content: str = _SAMPLE_MD, sha: str = "sha1"
) -> None:
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
    httpx_mock.add_response(
        json=[{"name": "002-other.md", "path": "requirements/002-other.md"}]
    )

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
        json={"number": 42, "title": "Fix bug", "html_url": "https://github.com/owner/repo/issues/42"},
    )

    result = await create_github_issues(
        "owner/repo",
        [{"title": "Fix bug", "body": "Details here"}],
    )

    assert result == [{"number": 42, "title": "Fix bug", "url": "https://github.com/owner/repo/issues/42"}]


async def test_create_github_issues_multiple(httpx_mock: HTTPXMock) -> None:
    """Multiple issues are created sequentially."""
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={"number": 1, "title": "First", "html_url": "https://github.com/owner/repo/issues/1"},
    )
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={"number": 2, "title": "Second", "html_url": "https://github.com/owner/repo/issues/2"},
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
        json={"number": 10, "title": "Labeled", "html_url": "https://github.com/owner/repo/issues/10"},
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
        json={"number": 11, "title": "No labels", "html_url": "https://github.com/owner/repo/issues/11"},
    )

    result = await create_github_issues(
        "owner/repo",
        [{"title": "No labels", "body": "Body"}],
    )

    assert result[0]["number"] == 11


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
