"""Unit tests for mcp/tools/specs.py."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from tools.specs import (
    get_capability,
    list_capabilities,
    propose_spec_change,
    spec_problems,
    update_spec_change,
    uses_specs,
)

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


# ---------------------------------------------------------------------------
# Format checking
# ---------------------------------------------------------------------------

_LANDED_SPECS = sorted((Path(__file__).parents[2] / "openspec" / "specs").glob("*/spec.md"))

_NO_SCENARIO_SPEC = """\
# Backlog

## Purpose

Issues are worked in one ordered backlog.

## Requirements

### Requirement: Issues are ordered

The system SHALL keep issues in order.
"""


@pytest.mark.parametrize("path", _LANDED_SPECS, ids=lambda p: p.parent.name)
def test_every_landed_spec_passes_the_format_check(path: Path) -> None:
    assert spec_problems(path.parent.name, path.read_text()) == []


def test_landed_specs_are_found() -> None:
    assert len(_LANDED_SPECS) > 1


@pytest.mark.parametrize(
    ("spec", "problem"),
    [
        (_BACKLOG_SPEC.replace("# Backlog\n", ""), "missing a '# Title' heading"),
        (
            _BACKLOG_SPEC.replace("# Backlog\n", "# Requirements\n"),
            "title must not be exactly 'Requirements'",
        ),
        (_BACKLOG_SPEC.replace("## Purpose", "## Aim"), "missing a '## Purpose' section"),
        (
            _BACKLOG_SPEC.replace("## Requirements", "## Rules"),
            "missing a '## Requirements' section",
        ),
        (
            _BACKLOG_SPEC.replace("SHALL keep", "shall keep"),
            "requirement 'Issues are ordered' must say SHALL or MUST",
        ),
        (
            _BACKLOG_SPEC.replace("SHALL keep", "MUSTER keep"),
            "requirement 'Issues are ordered' must say SHALL or MUST",
        ),
        (
            _BACKLOG_SPEC.replace(
                "Issues are ordered\n\nThe system SHALL keep issues in order.",
                "The system SHALL order issues\n\nIssues are kept in order.",
            ),
            "requirement 'The system SHALL order issues' must say SHALL or MUST",
        ),
        (
            _BACKLOG_SPEC.replace(
                "The system SHALL keep issues in order.", "Issues are kept in order."
            ).replace("- GIVEN two issues", "- GIVEN the system SHALL order two issues"),
            "requirement 'Issues are ordered' must say SHALL or MUST",
        ),
        (
            _BACKLOG_SPEC.replace("#### Scenario: Moving", "### Scenario: Moving"),
            "requirement 'Issues are ordered' has no '#### Scenario:' heading",
        ),
    ],
)
def test_format_check_rejects(spec: str, problem: str) -> None:
    problems = spec_problems("backlog", spec)
    assert any(p.startswith("backlog: ") and problem in p for p in problems), problems


def test_format_check_reports_every_problem_at_once() -> None:
    spec = _NO_SCENARIO_SPEC.replace("## Purpose", "## Aim").replace("SHALL", "may")

    assert spec_problems("backlog", spec) == [
        "backlog: missing a '## Purpose' section",
        "backlog: requirement 'Issues are ordered' must say SHALL or MUST in its text, "
        "not only in its heading or scenarios",
        "backlog: requirement 'Issues are ordered' has no '#### Scenario:' heading",
    ]


# ---------------------------------------------------------------------------
# propose_spec_change / update_spec_change
# ---------------------------------------------------------------------------

_ISSUES_PATH = "openspec/specs/issues/spec.md"
_PR_URL = f"https://github.com/{_REPO}/pull/12"


def _branch_file_url(path: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(f'{_API}/contents/{path}')}\?ref=")


def _mock_branch_file(
    httpx_mock: HTTPXMock, path: str, content: str | None, sha: str = "abc"
) -> None:
    if content is None:
        httpx_mock.add_response(
            method="GET", url=_branch_file_url(path), status_code=404, json={"message": "no"}
        )
    else:
        file = {"content": base64.b64encode(content.encode()).decode("ascii"), "sha": sha}
        httpx_mock.add_response(method="GET", url=_branch_file_url(path), json=file)


def _mock_put(httpx_mock: HTTPXMock, path: str, status_code: int = 200) -> None:
    httpx_mock.add_response(
        method="PUT", url=f"{_API}/contents/{path}", status_code=status_code, json={}
    )


def _mock_open_pull_request(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=f"{_API}/git/ref/heads/main", json={"object": {"sha": "m1"}})
    httpx_mock.add_response(method="POST", url=f"{_API}/git/refs", status_code=201, json={})
    httpx_mock.add_response(
        method="POST",
        url=f"{_API}/pulls",
        status_code=201,
        json={"number": 12, "html_url": _PR_URL, "node_id": "PR_12"},
    )
    httpx_mock.add_response(url=_API, json={"allow_auto_merge": False})


def _mock_open_spec_change(
    httpx_mock: HTTPXMock,
    *,
    state: str = "open",
    merged: bool = False,
    labels: tuple[str, ...] = ("spec-change",),
) -> None:
    httpx_mock.add_response(
        url=f"{_API}/pulls/12",
        json={
            "number": 12,
            "title": "Park issues",
            "html_url": _PR_URL,
            "state": state,
            "merged": merged,
            "head": {"ref": "spec-change/park-issues-20260926T000000Z"},
            "base": {"ref": "main"},
            "draft": False,
            "labels": [{"name": label} for label in labels],
        },
    )


def _requests(httpx_mock: HTTPXMock, method: str) -> list[httpx.Request]:
    return [r for r in httpx_mock.get_requests() if r.method == method]


def _written(request: httpx.Request) -> dict:
    body = json.loads(request.content)
    return {
        "path": request.url.path.removeprefix(f"/repos/{_REPO}/contents/"),
        "content": base64.b64decode(body["content"]).decode(),
        "sha": body.get("sha"),
        "branch": body["branch"],
    }


async def test_proposing_a_change_to_two_capabilities(httpx_mock: HTTPXMock) -> None:
    _mock_spec_repo(httpx_mock)
    _mock_open_pull_request(httpx_mock)
    _mock_branch_file(httpx_mock, _BACKLOG_PATH, "# Backlog (old)\n", sha="old")
    _mock_put(httpx_mock, _BACKLOG_PATH)
    _mock_branch_file(httpx_mock, _ISSUES_PATH, None)
    _mock_put(httpx_mock, _ISSUES_PATH, status_code=201)
    httpx_mock.add_response(method="POST", url=f"{_API}/issues/12/labels", json=[])

    result = await propose_spec_change(
        _REPO,
        "Park issues",
        "Some issues must wait.",
        {"backlog": _BACKLOG_SPEC, "issues": _ISSUES_SPEC},
    )

    assert result == {"repo": _REPO, "number": 12, "url": _PR_URL}
    [pull] = [r for r in _requests(httpx_mock, "POST") if r.url.path.endswith("/pulls")]
    assert json.loads(pull.content)["body"] == "Some issues must wait."
    branch = json.loads(pull.content)["head"]
    assert branch.startswith("spec-change/park-issues-")
    assert [_written(r) for r in _requests(httpx_mock, "PUT")] == [
        {"path": _BACKLOG_PATH, "content": _BACKLOG_SPEC, "sha": "old", "branch": branch},
        {"path": _ISSUES_PATH, "content": _ISSUES_SPEC, "sha": None, "branch": branch},
    ]
    [label] = [r for r in _requests(httpx_mock, "POST") if r.url.path.endswith("/labels")]
    assert json.loads(label.content) == {"labels": ["spec-change"]}


async def test_requirement_without_a_scenario(httpx_mock: HTTPXMock) -> None:
    with pytest.raises(ValueError) as refused:
        await propose_spec_change(
            _REPO, "Order", "Why", {"backlog": _NO_SCENARIO_SPEC, "issues": "# Issues\n"}
        )

    message = str(refused.value)
    assert "backlog: requirement 'Issues are ordered' has no '#### Scenario:' heading" in message
    assert "issues: missing a '## Purpose' section" in message
    assert "issues: missing a '## Requirements' section" in message


async def test_propose_spec_change_rejects_invalid_capability_names(httpx_mock: HTTPXMock) -> None:
    with pytest.raises(ValueError, match=r"Invalid capability name '\.\./secrets'"):
        await propose_spec_change(_REPO, "T", "Why", {"../secrets": None})


async def test_propose_spec_change_refused_in_repository_without_specs(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_not_spec_repo(httpx_mock)

    with pytest.raises(ValueError, match="does not use specs"):
        await propose_spec_change(_REPO, "T", "Why", {"backlog": _BACKLOG_SPEC})


async def test_propose_spec_change_removes_a_capability_mapped_to_null(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_spec_repo(httpx_mock)
    _mock_open_pull_request(httpx_mock)
    _mock_branch_file(httpx_mock, _ISSUES_PATH, _ISSUES_SPEC, sha="i1")
    httpx_mock.add_response(method="DELETE", url=f"{_API}/contents/{_ISSUES_PATH}", json={})
    httpx_mock.add_response(method="POST", url=f"{_API}/issues/12/labels", json=[])

    await propose_spec_change(_REPO, "Drop issues", "Why", {"issues": None})

    [delete] = _requests(httpx_mock, "DELETE")
    assert json.loads(delete.content)["sha"] == "i1"


async def test_propose_spec_change_explains_a_failed_label(httpx_mock: HTTPXMock) -> None:
    _mock_spec_repo(httpx_mock)
    _mock_open_pull_request(httpx_mock)
    _mock_branch_file(httpx_mock, _BACKLOG_PATH, None)
    _mock_put(httpx_mock, _BACKLOG_PATH, status_code=201)
    httpx_mock.add_response(method="POST", url=f"{_API}/issues/12/labels", status_code=403)

    with pytest.raises(RuntimeError, match=r"Opened spec change #12 .* could not label it"):
        await propose_spec_change(_REPO, "T", "Why", {"backlog": _BACKLOG_SPEC})


async def test_work_lands(httpx_mock: HTTPXMock) -> None:
    _mock_spec_repo(httpx_mock)
    _mock_open_pull_request(httpx_mock)
    _mock_branch_file(httpx_mock, _BACKLOG_PATH, None)
    _mock_put(httpx_mock, _BACKLOG_PATH, status_code=201)
    httpx_mock.add_response(method="POST", url=f"{_API}/issues/12/labels", json=[])

    await propose_spec_change(_REPO, "Park issues #42", "Closes #42", {"backlog": _BACKLOG_SPEC})

    assert [_written(r)["content"] for r in _requests(httpx_mock, "PUT")] == [_BACKLOG_SPEC]


async def test_extending_an_open_change(httpx_mock: HTTPXMock) -> None:
    _mock_open_spec_change(httpx_mock)
    _mock_branch_file(httpx_mock, _ISSUES_PATH, None)
    _mock_put(httpx_mock, _ISSUES_PATH, status_code=201)

    result = await update_spec_change(_REPO, 12, capabilities={"issues": _ISSUES_SPEC})

    assert result == {"repo": _REPO, "number": 12, "url": _PR_URL}
    [put] = _requests(httpx_mock, "PUT")
    assert _written(put) == {
        "path": _ISSUES_PATH,
        "content": _ISSUES_SPEC,
        "sha": None,
        "branch": "spec-change/park-issues-20260926T000000Z",
    }
    assert not _requests(httpx_mock, "POST")


async def test_update_spec_change_replaces_the_why(httpx_mock: HTTPXMock) -> None:
    _mock_open_spec_change(httpx_mock)
    httpx_mock.add_response(
        method="PATCH",
        url=f"{_API}/issues/12",
        json={"number": 12, "title": "Park issues", "html_url": _PR_URL},
    )

    await update_spec_change(_REPO, 12, why="A better reason.")

    [patch] = _requests(httpx_mock, "PATCH")
    assert json.loads(patch.content) == {"body": "A better reason."}


async def test_update_spec_change_checks_format_before_writing(httpx_mock: HTTPXMock) -> None:
    with pytest.raises(ValueError, match="has no '#### Scenario:' heading"):
        await update_spec_change(_REPO, 12, capabilities={"backlog": _NO_SCENARIO_SPEC})


async def test_update_spec_change_needs_something_to_change(httpx_mock: HTTPXMock) -> None:
    with pytest.raises(ValueError, match="Nothing to update"):
        await update_spec_change(_REPO, 12)


@pytest.mark.parametrize(
    ("state", "merged", "outcome"), [("closed", True, "merged"), ("closed", False, "been closed")]
)
async def test_update_spec_change_refused_once_merged_or_closed(
    httpx_mock: HTTPXMock, state: str, merged: bool, outcome: str
) -> None:
    _mock_open_spec_change(httpx_mock, state=state, merged=merged)

    with pytest.raises(ValueError, match=f"has {outcome} .* Propose a new spec change"):
        await update_spec_change(_REPO, 12, capabilities={"backlog": _BACKLOG_SPEC})


async def test_update_spec_change_refuses_a_pull_request_that_is_not_a_spec_change(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_open_spec_change(httpx_mock, labels=("enhancement",))

    with pytest.raises(ValueError, match="is not a spec change"):
        await update_spec_change(_REPO, 12, capabilities={"backlog": _BACKLOG_SPEC})


async def test_update_spec_change_rereads_the_file_when_its_branch_moved(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_open_spec_change(httpx_mock)
    _mock_branch_file(httpx_mock, _BACKLOG_PATH, "# Backlog (v1)\n", sha="v1")
    _mock_put(httpx_mock, _BACKLOG_PATH, status_code=409)
    _mock_branch_file(httpx_mock, _BACKLOG_PATH, "# Backlog (v2)\n", sha="v2")
    _mock_put(httpx_mock, _BACKLOG_PATH)

    await update_spec_change(_REPO, 12, capabilities={"backlog": _BACKLOG_SPEC})

    assert [_written(r)["sha"] for r in _requests(httpx_mock, "PUT")] == ["v1", "v2"]


async def test_update_spec_change_explains_a_persistent_conflict(httpx_mock: HTTPXMock) -> None:
    _mock_open_spec_change(httpx_mock)
    for attempt in range(3):
        _mock_branch_file(httpx_mock, _BACKLOG_PATH, "# Backlog (old)\n", sha=f"s{attempt}")
        _mock_put(httpx_mock, _BACKLOG_PATH, status_code=409)

    with pytest.raises(RuntimeError, match="Something else is changing that file"):
        await update_spec_change(_REPO, 12, capabilities={"backlog": _BACKLOG_SPEC})


async def test_update_spec_change_writes_repeatedly_with_each_current_version(
    httpx_mock: HTTPXMock,
) -> None:
    for version in range(1, 4):
        _mock_open_spec_change(httpx_mock)
        _mock_branch_file(httpx_mock, _BACKLOG_PATH, f"# Backlog (v{version})\n", f"v{version}")
        _mock_put(httpx_mock, _BACKLOG_PATH)

    for _ in range(3):
        await update_spec_change(_REPO, 12, capabilities={"backlog": _BACKLOG_SPEC})

    assert [_written(r)["sha"] for r in _requests(httpx_mock, "PUT")] == ["v1", "v2", "v3"]
