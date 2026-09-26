"""Unit tests for mcp/tools/specs.py."""

from __future__ import annotations

import base64

import pytest
from pytest_httpx import HTTPXMock

from tools.specs import get_capability, list_capabilities, uses_specs

_REPO = "owner/repo"
_API = f"https://api.github.com/repos/{_REPO}"
_SPECS_URL = f"{_API}/contents/openspec/specs"
_BACKLOG_PATH = "openspec/specs/backlog/spec.md"

_BACKLOG_SPEC = """\
# Backlog

## Purpose

Issues are worked in one ordered backlog.

## Requirements

### Requirement: Issues are ordered

The system SHALL keep issues in order.

#### Scenario: Moving an issue
- GIVEN two issues
- WHEN one is moved
- THEN the order changes

### Requirement: Issues can be parked

The system SHALL let an issue be parked.

#### Scenario: Parking
- GIVEN an issue
- WHEN it is parked
- THEN it is skipped
"""

_ISSUES_SPEC = """\
# Issues

## Purpose

Work is tracked as GitHub issues.

## Requirements

### Requirement: Issues are created

The system SHALL create issues.

#### Scenario: Creating
- GIVEN a requirement
- WHEN issues are created
- THEN they exist
"""


def _dir(name: str) -> dict:
    return {"name": name, "path": f"openspec/specs/{name}", "type": "dir"}


def _file(content: str) -> dict:
    return {"content": base64.b64encode(content.encode()).decode("ascii"), "sha": "abc"}


def _pull(number: int, labels: list[str]) -> dict:
    return {
        "number": number,
        "title": f"PR {number}",
        "html_url": f"https://github.com/{_REPO}/pull/{number}",
        "labels": [{"name": label} for label in labels],
    }


def _mock_spec_repo(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_SPECS_URL, json=[_dir("backlog"), _dir("issues")])


def _mock_not_spec_repo(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_SPECS_URL, status_code=404, json={"message": "Not Found"})


async def test_a_repository_with_specs_uses_spec_changes_detection(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_spec_repo(httpx_mock)
    assert await uses_specs(_REPO) is True


async def test_repository_without_openspec_specs_does_not_use_specs(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_not_spec_repo(httpx_mock)
    assert await uses_specs(_REPO) is False


async def test_pending_edit_is_visible(httpx_mock: HTTPXMock) -> None:
    _mock_spec_repo(httpx_mock)
    httpx_mock.add_response(url=f"{_API}/contents/{_BACKLOG_PATH}", json=_file(_BACKLOG_SPEC))
    httpx_mock.add_response(
        url=f"{_API}/pulls?state=open&per_page=100",
        json=[_pull(7, ["spec-change"]), _pull(8, ["spec-change"]), _pull(9, [])],
    )
    httpx_mock.add_response(
        url=f"{_API}/pulls/7/files?per_page=100", json=[{"filename": _BACKLOG_PATH}]
    )
    httpx_mock.add_response(
        url=f"{_API}/pulls/8/files?per_page=100",
        json=[{"filename": "openspec/specs/issues/spec.md"}],
    )

    result = await get_capability(_REPO, "backlog")

    assert result["uses_specs"] is True
    assert result["spec"] == _BACKLOG_SPEC
    assert result["spec_changes"] == [
        {"number": 7, "title": "PR 7", "url": f"https://github.com/{_REPO}/pull/7"}
    ]


async def test_spec_change_renaming_capability_file_away_touches_it(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_spec_repo(httpx_mock)
    httpx_mock.add_response(url=f"{_API}/contents/{_BACKLOG_PATH}", json=_file(_BACKLOG_SPEC))
    httpx_mock.add_response(
        url=f"{_API}/pulls?state=open&per_page=100", json=[_pull(7, ["spec-change"])]
    )
    httpx_mock.add_response(
        url=f"{_API}/pulls/7/files?per_page=100",
        json=[
            {
                "filename": "openspec/specs/queue/spec.md",
                "previous_filename": _BACKLOG_PATH,
            }
        ],
    )

    result = await get_capability(_REPO, "backlog")

    assert [pr["number"] for pr in result["spec_changes"]] == [7]


async def test_list_capabilities_reports_name_purpose_and_requirement_count(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=_SPECS_URL,
        json=[
            _dir("issues"),
            _dir("backlog"),
            {"name": "README.md", "path": "openspec/specs/README.md", "type": "file"},
        ],
    )
    httpx_mock.add_response(url=f"{_API}/contents/{_BACKLOG_PATH}", json=_file(_BACKLOG_SPEC))
    httpx_mock.add_response(
        url=f"{_API}/contents/openspec/specs/issues/spec.md", json=_file(_ISSUES_SPEC)
    )

    result = await list_capabilities(_REPO)

    assert result == {
        "repo": _REPO,
        "uses_specs": True,
        "capabilities": [
            {
                "name": "backlog",
                "purpose": "Issues are worked in one ordered backlog.",
                "requirement_count": 2,
            },
            {
                "name": "issues",
                "purpose": "Work is tracked as GitHub issues.",
                "requirement_count": 1,
            },
        ],
    }


async def test_list_capabilities_in_repository_without_specs_says_so(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_not_spec_repo(httpx_mock)

    result = await list_capabilities(_REPO)

    assert result["uses_specs"] is False
    assert "does not use specs" in result["message"]


async def test_get_capability_in_repository_without_specs_says_so(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_not_spec_repo(httpx_mock)

    result = await get_capability(_REPO, "backlog")

    assert result["uses_specs"] is False
    assert "does not use specs" in result["message"]


async def test_get_capability_raises_when_capability_missing(httpx_mock: HTTPXMock) -> None:
    _mock_spec_repo(httpx_mock)
    httpx_mock.add_response(
        url=f"{_API}/contents/openspec/specs/nope/spec.md",
        status_code=404,
        json={"message": "Not Found"},
    )

    with pytest.raises(FileNotFoundError, match="Capability 'nope' not found"):
        await get_capability(_REPO, "nope")


@pytest.mark.parametrize("name", ["", "../secrets", "backlog/spec.md", ".hidden"])
async def test_get_capability_rejects_names_that_are_not_a_folder(name: str) -> None:
    with pytest.raises(ValueError, match="Invalid capability name"):
        await get_capability(_REPO, name)
