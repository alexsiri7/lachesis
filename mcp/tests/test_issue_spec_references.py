"""Tests for issues saying what they implement: the spec reference block."""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

import github
from spec_reference import SpecReference, append_reference, parse_reference
from tools.requirements import create_github_issues

_REPO = "owner/repo"
_API = f"https://api.github.com/repos/{_REPO}"
_SPECS_URL = f"{_API}/contents/openspec/specs"
_ISSUES_URL = f"{_API}/issues"
_LABELS_URL = f"{_API}/labels"


def _mock_spec_repo(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_SPECS_URL,
        json=[{"name": "backlog", "path": "openspec/specs/backlog", "type": "dir"}],
    )


def _mock_created(httpx_mock: HTTPXMock, number: int) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_ISSUES_URL,
        json={
            "id": 1000 + number,
            "number": number,
            "title": f"Issue {number}",
            "html_url": f"https://github.com/{_REPO}/issues/{number}",
            "node_id": f"I_{number}",
        },
    )


def _created_issues(httpx_mock: HTTPXMock) -> list[dict]:
    return [
        json.loads(request.content)
        for request in httpx_mock.get_requests(method="POST", url=_ISSUES_URL)
    ]


# ---------------------------------------------------------------------------
# Scenarios of issues / "Issues say what they implement"
# ---------------------------------------------------------------------------


async def test_implementing_a_spec_change(httpx_mock: HTTPXMock) -> None:
    _mock_spec_repo(httpx_mock)
    _mock_created(httpx_mock, 160)

    await create_github_issues(
        _REPO,
        [
            {
                "title": "Say what to work on next",
                "body": "Build it.\n",
                "spec_change": 152,
                "capability": "backlog",
                "requirement": "The system says what to work on next",
            }
        ],
    )

    [issue] = _created_issues(httpx_mock)
    assert issue["body"] == (
        "Build it.\n"
        "\n"
        "---\n"
        "Spec change: #152\n"
        "Capability: backlog\n"
        "Requirement: The system says what to work on next"
    )
    assert "labels" not in issue


async def test_audit_finding(httpx_mock: HTTPXMock) -> None:
    _mock_spec_repo(httpx_mock)
    httpx_mock.add_response(method="POST", url=_LABELS_URL, status_code=201, json={})
    _mock_created(httpx_mock, 161)

    await create_github_issues(
        _REPO,
        [
            {
                "title": "Backlog order ignores parked issues",
                "body": "Found in audit.",
                "labels": ["bug"],
                "spec_gap": True,
                "capability": "backlog",
                "requirement": "The system says what to work on next",
            }
        ],
    )

    [issue] = _created_issues(httpx_mock)
    assert issue["labels"] == ["bug", "spec-gap"]
    assert issue["body"].endswith(
        "\n\n---\nSpec gap: yes\nCapability: backlog\n"
        "Requirement: The system says what to work on next"
    )
    assert json.loads(httpx_mock.get_request(url=_LABELS_URL).content)["name"] == "spec-gap"


async def test_no_reference(httpx_mock: HTTPXMock) -> None:
    _mock_spec_repo(httpx_mock)

    with pytest.raises(ValueError) as excinfo:
        await create_github_issues(
            _REPO,
            [
                {"title": "Referenced", "body": "b", "spec_change": 152, "capability": "backlog"},
                {"title": "Unreferenced", "body": "b", "capability": "backlog"},
            ],
        )

    message = str(excinfo.value)
    assert "issue 2 ('Unreferenced') needs exactly one of spec_change or spec_gap: true" in message
    assert "issue 1" not in message
    assert _created_issues(httpx_mock) == []


# ---------------------------------------------------------------------------
# Refusals name what is missing, before anything is created
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("issue", "problem"),
    [
        ({"spec_change": 152}, "capability"),
        ({"spec_gap": True, "capability": "  "}, "capability"),
        (
            {"spec_change": 152, "spec_gap": True, "capability": "backlog"},
            "exactly one of spec_change or spec_gap: true",
        ),
        ({"spec_gap": False, "capability": "backlog"}, "exactly one of"),
        ({"spec_change": "#152", "capability": "backlog"}, "spec_change as a pull request number"),
        ({"spec_change": True, "capability": "backlog"}, "spec_change as a pull request number"),
        ({"spec_gap": "yes", "capability": "backlog"}, "spec_gap as true or false"),
        ({"spec_gap": True, "capability": "backlog", "requirement": 3}, "requirement as a string"),
        ({"spec_gap": True, "capability": "back\nlog"}, "capability on one line"),
        (
            {"spec_gap": True, "capability": "backlog", "requirement": "a\r\nb"},
            "requirement on one line",
        ),
    ],
)
async def test_incomplete_reference_is_refused(
    httpx_mock: HTTPXMock, issue: dict, problem: str
) -> None:
    _mock_spec_repo(httpx_mock)

    with pytest.raises(ValueError, match=f"issue 1 \\('T'\\) needs .*{problem}"):
        await create_github_issues(_REPO, [{"title": "T", "body": "b", **issue}])

    assert _created_issues(httpx_mock) == []


async def test_referenced_issues_still_wire_dependencies(httpx_mock: HTTPXMock) -> None:
    _mock_spec_repo(httpx_mock)
    _mock_created(httpx_mock, 1)
    _mock_created(httpx_mock, 2)
    dependency_url = f"{_ISSUES_URL}/2/dependencies/blocked_by"
    httpx_mock.add_response(method="POST", url=dependency_url, json={})

    result = await create_github_issues(
        _REPO,
        [
            {"title": "A", "body": "a", "spec_change": 152, "capability": "backlog"},
            {
                "title": "B",
                "body": "b",
                "spec_change": 152,
                "capability": "backlog",
                "blocked_by": [1],
            },
        ],
    )

    assert result[1]["blocked_by"] == [1]
    assert json.loads(httpx_mock.get_request(url=dependency_url).content) == {"issue_id": 1001}
    for issue in _created_issues(httpx_mock):
        assert issue["body"].endswith("\n\n---\nSpec change: #152\nCapability: backlog")


async def test_repository_without_specs_ignores_reference_fields(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_SPECS_URL, status_code=404, json={"message": "Not Found"})
    _mock_created(httpx_mock, 5)
    _mock_created(httpx_mock, 6)

    await create_github_issues(
        _REPO,
        [
            {"title": "Plain", "body": "Body"},
            {"title": "Gap", "body": "Body", "spec_gap": True, "capability": "backlog"},
        ],
    )

    assert _created_issues(httpx_mock) == [
        {"title": "Plain", "body": "Body"},
        {"title": "Gap", "body": "Body"},
    ]


# ---------------------------------------------------------------------------
# github.ensure_label
# ---------------------------------------------------------------------------


async def test_ensure_label_tolerates_an_existing_label(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_LABELS_URL,
        status_code=422,
        json={"message": "Validation Failed", "errors": [{"code": "already_exists"}]},
    )

    await github.ensure_label(_REPO, "spec-gap", "d93f0b")


async def test_ensure_label_raises_on_other_failures(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_LABELS_URL,
        status_code=422,
        json={"message": "Validation Failed", "errors": [{"code": "invalid"}]},
    )

    with pytest.raises(httpx.HTTPStatusError):
        await github.ensure_label(_REPO, "spec-gap", "d93f0b")


async def test_failed_label_creation_creates_no_issues(httpx_mock: HTTPXMock) -> None:
    _mock_spec_repo(httpx_mock)
    httpx_mock.add_response(method="POST", url=_LABELS_URL, status_code=403, json={})

    with pytest.raises(httpx.HTTPStatusError):
        await create_github_issues(
            _REPO, [{"title": "T", "body": "b", "spec_gap": True, "capability": "backlog"}]
        )

    assert _created_issues(httpx_mock) == []


# ---------------------------------------------------------------------------
# Reading the block back
# ---------------------------------------------------------------------------

_CHANGE = SpecReference(152, "backlog", "The system says what to work on next")
_GAP = SpecReference(None, "issues")


@pytest.mark.parametrize("ref", [_CHANGE, _GAP])
def test_parse_reads_back_what_was_written(ref: SpecReference) -> None:
    assert parse_reference(append_reference("Some work.", ref)) == ref


def test_parse_tolerates_crlf_and_trailing_whitespace() -> None:
    body = "Work.\r\n\r\n---  \r\nSpec change: #152 \r\nCapability: backlog\t\r\n\r\n"
    assert parse_reference(body) == SpecReference(152, "backlog")


def test_parse_reads_only_after_the_last_rule() -> None:
    body = "Intro\n\n---\n\nCapability: not this\n\n" + append_reference("", _GAP)
    assert parse_reference(body) == _GAP


@pytest.mark.parametrize(
    "body",
    [
        "No block at all.",
        "Text\n\n---\nMore text",
        "---\nCapability: backlog",
        "---\nSpec change: #152",
        "---\nSpec change: #152\nSpec gap: yes\nCapability: backlog",
        "---\nSpec gap: no\nCapability: backlog",
        "---\nSpec change: 152\nCapability: backlog",
        "---\nSpec change: #152\nCapability: backlog\nOther: x",
    ],
)
def test_parse_returns_none_without_a_complete_block(body: str) -> None:
    assert parse_reference(body) is None
