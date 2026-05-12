"""Unit tests for mcp/tools/requirements.py — link_issues."""

from __future__ import annotations

import base64
import json
from datetime import date

import frontmatter
import pytest
from pytest_httpx import HTTPXMock

from tools.requirements import create_github_issues, delete_requirement, link_issues

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
    """Missing labels key does not cause KeyError and labels is omitted from request."""
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
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert "labels" not in body


async def test_create_github_issues_http_error_propagates(httpx_mock: HTTPXMock) -> None:
    """HTTP error from GitHub API propagates as HTTPStatusError."""
    import httpx

    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={"number": 1, "title": "First", "html_url": "https://github.com/owner/repo/issues/1"},
    )
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        status_code=422,
        json={"message": "Validation Failed"},
    )

    with pytest.raises(httpx.HTTPStatusError):
        await create_github_issues(
            "owner/repo",
            [
                {"title": "First", "body": "Body 1"},
                {"title": "Second", "body": "Body 2"},
            ],
        )


async def test_create_github_issues_missing_body_raises(httpx_mock: HTTPXMock) -> None:
    """Issue dict missing 'body' raises ValueError with clear message."""
    with pytest.raises(ValueError, match="index 0"):
        await create_github_issues(
            "owner/repo",
            [{"title": "No body"}],
        )


async def test_create_github_issues_missing_title_raises(httpx_mock: HTTPXMock) -> None:
    """Issue dict missing 'title' raises ValueError with clear message."""
    with pytest.raises(ValueError, match="index 1"):
        await create_github_issues(
            "owner/repo",
            [
                {"title": "Valid", "body": "Body"},
                {"body": "Missing title"},
            ],
        )
