"""The progress-reporting capability's scenarios, one test named after each.

See ``openspec/specs/progress-reporting/spec.md``.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport
from pytest_httpx import HTTPXMock

from auth import current_db_user_id
from derive import IssueFacts, derive, derive_spec_change
from groups import Candidate, group_requirements
from landing import SpecChangeCandidate
from spec_reference import SpecReference
from tools.requirements import get_dashboard, list_requirements
from tools.specs import (
    list_spec_changes,
    patch_scenarios,
    pending_scenarios,
    read_spec_progress,
    scenario_view,
    spec_change_status,
)

_GRAPHQL_URL = "https://api.github.com/graphql"
_NOW = datetime.now(UTC)


def _stamp(days_ago: int) -> str:
    return (_NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reference(spec_change: int | None, capability: str = "backlog") -> str:
    kind = "Spec gap: yes" if spec_change is None else f"Spec change: #{spec_change}"
    return f"Some work.\n\n---\n{kind}\nCapability: {capability}"


def _issue_item(
    number: int,
    *,
    body: str = "",
    state: str = "open",
    labels: tuple[str, ...] = (),
    repo: str = "owner/repo",
) -> dict[str, Any]:
    """An issue as GET /repos/{repo}/issues lists it."""
    return {
        "number": number,
        "title": f"Issue {number}",
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "body": body,
        "state": state,
        "labels": [{"name": label} for label in labels],
        "created_at": _stamp(30),
        "updated_at": _stamp(20),
    }


def _spec_change_item(
    number: int,
    *,
    state: str = "closed",
    merged_at: str | None = None,
    updated_at: str | None = None,
    repo: str = "owner/repo",
) -> dict[str, Any]:
    """A pull request labelled spec-change as GET /repos/{repo}/issues lists it."""
    item = _issue_item(number, state=state, labels=("spec-change",), repo=repo)
    item["html_url"] = f"https://github.com/{repo}/pull/{number}"
    item["pull_request"] = {"merged_at": merged_at}
    item["updated_at"] = updated_at or merged_at or _stamp(20)
    return item


def _merged_pull_node(number: int) -> dict:
    """A GraphQL pull request node as the issue-activity query returns it."""
    return {
        "number": number,
        "state": "MERGED",
        "merged": True,
        "updatedAt": _stamp(2),
        "mergedAt": _stamp(2),
        "isDraft": False,
        "commits": {"nodes": []},
    }


def _issue_node(number: int, *, state: str = "OPEN", merged_pr: int | None = None) -> dict:
    """A GraphQL issue node as the issue-activity query returns it."""
    pulls = [] if merged_pr is None else [_merged_pull_node(merged_pr)]
    return {
        "number": number,
        "state": state,
        "updatedAt": _stamp(2),
        "closedAt": _stamp(2) if state == "CLOSED" else None,
        "labels": {"nodes": []},
        "timelineItems": {"nodes": []},
        "closedByPullRequestsReferences": {"nodes": pulls},
    }


def _mock_uses_specs(httpx_mock: HTTPXMock, repo: str = "owner/repo") -> None:
    httpx_mock.add_response(
        url=f"https://api.github.com/repos/{repo}/contents/openspec/specs",
        json=[{"name": "backlog", "path": "openspec/specs/backlog", "type": "dir"}],
    )


def _mock_issue_activity(httpx_mock: HTTPXMock, *nodes: dict) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"repository": {f"i{node['number']}": node for node in nodes}}},
    )


def _mock_comments(httpx_mock: HTTPXMock, **comments: list[tuple[str, str]]) -> None:
    """Answer the comment read for closed issues with nothing merged, keyed ``c<number>``."""
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={
            "data": {
                "repository": {
                    alias: {
                        "number": int(alias[1:]),
                        "comments": {
                            "nodes": [
                                {"authorAssociation": association, "body": body}
                                for association, body in nodes
                            ]
                        },
                    }
                    for alias, nodes in comments.items()
                }
            }
        },
    )


def _waiting_node(
    number: int, *, labelled_at: str, comments: tuple[tuple[str, str], ...]
) -> dict[str, Any]:
    """A GraphQL issue node as the waiting-on-author query returns it."""
    return {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://github.com/owner/repo/issues/{number}",
        "createdAt": _stamp(30),
        "timelineItems": {
            "nodes": [
                {"createdAt": _stamp(40), "label": {"name": "spec-gap"}},
                {"createdAt": labelled_at, "label": {"name": "needs-author"}},
            ]
        },
        "comments": {"nodes": [{"body": body, "createdAt": at} for at, body in comments]},
    }


def _mock_waiting(httpx_mock: HTTPXMock, *nodes: dict[str, Any]) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"repository": {"issues": {"nodes": list(nodes)}}}},
    )


def _facts(number: int, *, state: str = "OPEN", merged: bool = False) -> IssueFacts:
    return {
        "number": number,
        "state": state,
        "updated_at": "2026-08-01T00:00:00Z",
        "closed_at": "2026-08-01T00:00:00Z" if state == "CLOSED" else None,
        "labels": [],
        "closed_by_commit": False,
        "blocked_by": [],
        "pulls": [
            {
                "number": 900 + number,
                "state": "MERGED",
                "merged": True,
                "updated_at": "2026-08-01T00:00:00Z",
                "merged_at": "2026-08-01T00:00:00Z",
                "merge_commit": "abc",
                "is_draft": False,
                "checks": [],
            }
        ]
        if merged
        else [],
    }


def _group_of(grouped: dict[str, list[dict[str, Any]]], **identity: Any) -> str:
    [name] = [
        name
        for name, entries in grouped.items()
        for entry in entries
        if all(entry.get(key) == value for key, value in identity.items())
    ]
    return name


@pytest.fixture()
def signed_in() -> Any:
    token = current_db_user_id.set("test-uid")
    yield
    current_db_user_id.reset(token)


_ISSUES_URL = re.compile(r"https://api\.github\.com/repos/owner/repo/issues\?.*")
_CHECK_RUNS_URL = re.compile(
    r"https://api\.github\.com/repos/[^/]+/[^/]+/commits/main/check-runs.*"
)


@pytest.fixture()
def scenario_results() -> dict[str, dict[str, str]] | int | None:
    """The scenario results CI published for the default branch: none fail unless a test
    says.  A status code is GitHub refusing the read."""
    return {"backlog": {"Ordered": "passed"}}


@pytest.fixture(autouse=True)
def _published(
    request: pytest.FixtureRequest, scenario_results: dict[str, dict[str, str]] | int | None
) -> None:
    if "httpx_mock" not in request.fixturenames:
        return
    httpx_mock: HTTPXMock = request.getfixturevalue("httpx_mock")
    if isinstance(scenario_results, int):
        httpx_mock.add_response(
            url=_CHECK_RUNS_URL,
            status_code=scenario_results,
            json={"message": "Resource not accessible by personal access token"},
            is_optional=True,
        )
        return
    if scenario_results is None:
        httpx_mock.add_response(
            url=_CHECK_RUNS_URL, json={"total_count": 0, "check_runs": []}, is_optional=True
        )
        return
    httpx_mock.add_response(
        url=_CHECK_RUNS_URL,
        json={
            "total_count": 1,
            "check_runs": [
                {
                    "name": "scenario-results",
                    "head_sha": "c0ffee",
                    "status": "completed",
                    "completed_at": _stamp(0),
                    "output": {"text": json.dumps(scenario_results)},
                }
            ],
        },
        is_optional=True,
        is_reusable=True,
    )


def _mock_spec_change_files(
    httpx_mock: HTTPXMock, number: int, patch: str | None, capability: str = "backlog"
) -> None:
    httpx_mock.add_response(
        url=f"https://api.github.com/repos/owner/repo/pulls/{number}/files?per_page=100",
        json=[
            {
                "filename": f"openspec/specs/{capability}/spec.md",
                "status": "modified",
                **({} if patch is None else {"patch": patch}),
            },
            {"filename": "README.md", "status": "modified", "patch": "@@ -1 +1 @@\n+Hi"},
        ],
    )


async def _list_built_spec_change(httpx_mock: HTTPXMock, number: int = 170) -> dict[str, Any]:
    """List merged spec change *number*, whose one issue closed with a merged pull request."""
    _mock_uses_specs(httpx_mock)
    httpx_mock.add_response(
        url=_ISSUES_URL,
        json=[
            _issue_item(number + 1, body=_reference(number), state="closed"),
            _spec_change_item(number, merged_at=_stamp(5)),
        ],
    )
    _mock_issue_activity(httpx_mock, _issue_node(number + 1, state="CLOSED", merged_pr=300))
    [change] = await list_spec_changes("owner/repo")
    return change


# ---------------------------------------------------------------------------
# Status is derived from the work
# ---------------------------------------------------------------------------


async def test_spec_change_fully_built(httpx_mock: HTTPXMock) -> None:
    _mock_uses_specs(httpx_mock)
    httpx_mock.add_response(
        json=[
            _issue_item(203, body=_reference(152), state="closed"),
            _issue_item(202, body=_reference(152), state="closed"),
            _issue_item(201, body=_reference(152), state="closed"),
            _issue_item(200, body=_reference(151), state="closed"),
            _spec_change_item(152, merged_at=_stamp(5)),
        ]
    )
    _mock_issue_activity(
        httpx_mock,
        *(_issue_node(n, state="CLOSED", merged_pr=300 + n) for n in (201, 202, 203)),
    )

    [change] = await list_spec_changes("owner/repo")

    assert change["spec_change"] == 152
    assert change["state"] == "merged"
    assert change["derived"]["status"] == "done"
    assert change["derived"]["linked_issues"] == [201, 202, 203]
    assert change["derived"]["diverged"] is False
    grouped = group_requirements(
        [Candidate("owner/repo", change, change["derived"]["linked_issues"])],
        now=_NOW,
        stale_after_days=14,
    )
    assert _group_of(grouped, spec_change=152) == "landed"


_THREE_SCENARIOS = (
    "@@ -1,3 +1,12 @@\n"
    " ## Requirements\n"
    "+#### Scenario: Ordered\n"
    "+- GIVEN an order\n"
    "+#### Scenario: Parked\n"
    "+- GIVEN a parked issue\n"
    "+#### Scenario: Focused\n"
    "+- GIVEN a focus"
)


@pytest.mark.parametrize(
    "scenario_results",
    [{"backlog": {"Ordered": "passed", "Parked": "xfail", "Focused": "failed", "Held": "xfail"}}],
)
async def test_built_but_not_yet_passing(httpx_mock: HTTPXMock) -> None:
    _mock_spec_change_files(httpx_mock, 170, _THREE_SCENARIOS)

    change = await _list_built_spec_change(httpx_mock)

    assert change["derived"]["status"] == "in-progress"
    assert change["derived"]["landed_at"] is None
    assert change["pending_scenarios"] == [
        {"capability": "backlog", "scenario": "Parked", "result": "xfail"},
        {"capability": "backlog", "scenario": "Focused", "result": "failed"},
    ]


@pytest.mark.parametrize("scenario_results", [{"backlog": {"Ordered": "xfail"}}])
async def test_a_built_spec_change_is_done_once_its_own_scenarios_pass(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_spec_change_files(httpx_mock, 170, "@@ -1,2 +1,4 @@\n+#### Scenario: Parked\n+- GIVEN")

    change = await _list_built_spec_change(httpx_mock)

    assert change["derived"]["status"] == "done"
    assert change["pending_scenarios"] == []


@pytest.mark.parametrize("scenario_results", [None])
async def test_a_built_spec_change_waits_for_scenario_results_to_be_published(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_spec_change_files(httpx_mock, 170, "@@ -1,2 +1,4 @@\n+#### Scenario: Parked\n+- GIVEN")

    change = await _list_built_spec_change(httpx_mock)

    assert change["derived"]["status"] == "in-progress"
    assert change["pending_scenarios"] == [
        {"capability": "backlog", "scenario": "Parked", "result": None}
    ]


@pytest.mark.parametrize("scenario_results", [{"backlog": {"Parked": "xfail"}}])
async def test_a_change_the_diff_cannot_place_is_found_in_the_spec_at_the_head(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_spec_change_files(httpx_mock, 170, "@@ -7,3 +7,3 @@\n context\n-- THEN old\n+- THEN new")
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/pulls/170",
        json={"head": {"sha": "head1"}, "base": {"sha": "base0"}},
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/compare/base0...head1",
        json={"merge_base_commit": {"sha": "base1"}},
    )
    spec = "\n".join(
        [
            "# Backlog",
            "## Requirements",
            "### Requirement: Park",
            "x SHALL",
            "#### Scenario: Parked",
        ]
        + ["- GIVEN", "- WHEN", "- THEN new", "- AND more"]
    )
    httpx_mock.add_response(
        url=(
            "https://api.github.com/repos/owner/repo/contents/openspec/specs/backlog/spec.md"
            "?ref=head1"
        ),
        content=spec.encode(),
    )

    change = await _list_built_spec_change(httpx_mock)

    assert change["pending_scenarios"] == [
        {"capability": "backlog", "scenario": "Parked", "result": "xfail"}
    ]


@pytest.mark.parametrize(
    "scenario_results", [{"backlog": {"Ordered": "passed", "Parked": "xfail", "Held": "failed"}}]
)
async def test_every_scenario_of_a_spec_whose_diff_github_withholds_is_touched(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_spec_change_files(httpx_mock, 170, None)
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/pulls/170",
        json={"head": {"sha": "head1"}, "base": {"sha": "base0"}},
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/compare/base0...head1",
        json={"merge_base_commit": {"sha": "base1"}},
    )
    spec = "\n".join(
        [
            "# Backlog",
            "## Requirements",
            "### Requirement: Park",
            "x SHALL",
            "#### Scenario: Ordered",
            "- GIVEN",
            "#### Scenario: Parked",
            "- GIVEN",
            "#### Scenario: Held",
            "- GIVEN",
        ]
    )
    httpx_mock.add_response(
        url=(
            "https://api.github.com/repos/owner/repo/contents/openspec/specs/backlog/spec.md"
            "?ref=head1"
        ),
        content=spec.encode(),
    )

    change = await _list_built_spec_change(httpx_mock)

    assert change["derived"]["status"] == "in-progress"
    assert change["pending_scenarios"] == [
        {"capability": "backlog", "scenario": "Parked", "result": "xfail"},
        {"capability": "backlog", "scenario": "Held", "result": "failed"},
    ]


@pytest.mark.parametrize("scenario_results", [{"backlog": {"Parked": "xfail"}}])
async def test_a_built_spec_change_github_will_not_diff_is_unreadable(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/pulls/170/files?per_page=100",
        status_code=502,
    )

    change = await _list_built_spec_change(httpx_mock)

    assert change["derived"]["status"] is None
    assert change["derived"]["source"] == "unavailable"
    assert change["derived"]["unavailable_cause"] == "transient"
    assert change["derived"]["landed_at"] is None
    assert spec_change_status(change) == "unreadable"


def test_a_patch_names_the_scenarios_it_adds_or_modifies() -> None:
    patch = (
        "@@ -1,4 +1,5 @@\n"
        " #### Scenario: Kept\n"
        " - GIVEN a widget\n"
        "+- AND another\n"
        " #### Scenario: Untouched\n"
        " - GIVEN nothing\n"
        "@@ -20,3 +21,3 @@\n"
        "-- THEN gone\n"
        "+#### Scenario: Added\n"
        " ### Requirement: Next\n"
        "+Some text SHALL hold.\n"
        "\\ No newline at end of file"
    )

    assert patch_scenarios(patch) == (["Kept", "Added"], [20])


def test_a_scenario_no_longer_specified_is_not_pending() -> None:
    results = {"commit": "c0ffee", "capabilities": {"backlog": {"Parked": "untested"}}}

    pending = pending_scenarios([("backlog", "Parked"), ("backlog", "Removed")], results)

    assert pending == [{"capability": "backlog", "scenario": "Parked", "result": "untested"}]


def test_capability_view() -> None:
    passing = ["Ordered", "Focused", "Interrupted", "Drawn", "Held"]
    results = {
        "commit": "c0ffee",
        "capabilities": {"backlog": {**{title: "passed" for title in passing}, "Parked": "xfail"}},
    }

    view = scenario_view(results)

    assert view == {
        "published": True,
        "commit": "c0ffee",
        "capabilities": {
            "backlog": {"passed": passing, "xfail": ["Parked"], "failed": [], "untested": []}
        },
    }


def test_requirement_file_shipped_but_never_updated() -> None:
    facts = {n: _facts(n, state="CLOSED", merged=True) for n in (42, 43)}

    derived = derive("idea", [42, 43], facts, on_pr_branch=False)

    assert derived["status"] == "done"


# ---------------------------------------------------------------------------
# Disagreement is reported, and author intent wins
# ---------------------------------------------------------------------------


def test_diverged_requirement_file() -> None:
    facts = {42: _facts(42, state="CLOSED", merged=True)}
    entry = {
        "id": "001",
        "title": "Requirement 001",
        "status": "idea",
        "derived": derive("idea", [42], facts, on_pr_branch=False),
    }

    assert entry["derived"]["status"] == "done"
    assert entry["derived"]["diverged"] is True
    [grouped] = [
        e
        for group in group_requirements(
            [Candidate("owner/repo", entry, [42])], now=_NOW, stale_after_days=14
        ).values()
        for e in group
    ]
    assert (grouped["status"], grouped["derived_status"]) == ("idea", "done")


def test_a_spec_change_is_never_diverged() -> None:
    facts = {42: _facts(42, state="CLOSED", merged=True)}

    derived = derive_spec_change("merged", None, [42], facts)

    assert derived["status"] == "done"
    assert derived["diverged"] is False
    assert derived["signals"]["closed_but_recorded_in_progress"] is False


def test_a_spec_change_is_not_done_before_its_pull_request_merges() -> None:
    facts = {42: _facts(42, state="CLOSED", merged=True)}

    derived = derive_spec_change("open", None, [42], facts)

    assert derived["status"] == "in-progress"
    assert (derived["landed_at"], derived["landed_commit"]) == (None, None)


def test_a_spec_change_with_an_unread_issue_is_unreadable_not_done() -> None:
    facts = {1: _facts(1, state="CLOSED", merged=True)}

    derived = derive_spec_change("merged", None, [1, 2], facts, failure="transient")

    assert derived["status"] is None
    assert (derived["source"], derived["unavailable_cause"]) == ("unavailable", "transient")


# ---------------------------------------------------------------------------
# Dropped work is recognised
# ---------------------------------------------------------------------------


async def test_spec_change_abandoned(httpx_mock: HTTPXMock) -> None:
    _mock_uses_specs(httpx_mock)
    httpx_mock.add_response(url=_ISSUES_URL, json=[_spec_change_item(160, state="closed")])

    assert await list_spec_changes("owner/repo") == []

    _mock_uses_specs(httpx_mock)
    httpx_mock.add_response(url=_ISSUES_URL, json=[_spec_change_item(160, state="closed")])
    [change] = await list_spec_changes("owner/repo", include_inactive=True)

    assert change["state"] == "closed"
    assert change["dropped"] is True
    assert change["reason"] == "its pull request closed without merging"
    grouped = group_requirements(
        [Candidate("owner/repo", change, [])], now=_NOW, stale_after_days=14
    )
    assert _group_of(grouped, spec_change=160) == "dropped"
    assert grouped["dropped"][0]["reason"] == "its pull request closed without merging"


async def test_a_spec_change_whose_every_issue_closed_unmerged_is_dropped(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_uses_specs(httpx_mock)
    httpx_mock.add_response(
        json=[
            _issue_item(201, body=_reference(152), state="closed"),
            _issue_item(202, body=_reference(152), state="closed"),
            _spec_change_item(152, merged_at=_stamp(5)),
        ]
    )
    _mock_issue_activity(
        httpx_mock, _issue_node(201, state="CLOSED"), _issue_node(202, state="CLOSED")
    )
    _mock_comments(httpx_mock, c201=[], c202=[])

    [change] = await list_spec_changes("owner/repo", include_inactive=True)

    assert change["dropped"] is True
    assert change["reason"] == "every issue naming it closed without a merged pull request"


async def test_a_spec_change_with_one_issue_closed_unmerged_needs_attention(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_uses_specs(httpx_mock)
    httpx_mock.add_response(
        json=[
            _issue_item(201, body=_reference(152), state="closed"),
            _issue_item(202, body=_reference(152), state="closed"),
            _spec_change_item(152, merged_at=_stamp(5)),
        ]
    )
    _mock_issue_activity(
        httpx_mock,
        _issue_node(201, state="CLOSED", merged_pr=301),
        _issue_node(202, state="CLOSED"),
    )
    _mock_comments(httpx_mock, c202=[])

    [change] = await list_spec_changes("owner/repo")

    assert change["dropped"] is False
    grouped = group_requirements(
        [Candidate("owner/repo", change, change["derived"]["linked_issues"])],
        now=_NOW,
        stale_after_days=14,
    )
    assert _group_of(grouped, spec_change=152) == "needs_attention"


async def test_issue_closed_by_hand_naming_the_merged_pull_request(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_uses_specs(httpx_mock)
    httpx_mock.add_response(
        json=[
            _issue_item(201, body=_reference(152), state="closed"),
            _issue_item(202, body=_reference(152), state="closed"),
            _spec_change_item(152, merged_at=_stamp(5)),
        ]
    )
    _mock_issue_activity(
        httpx_mock,
        _issue_node(201, state="CLOSED", merged_pr=301),
        _issue_node(202, state="CLOSED"),
    )
    _mock_comments(httpx_mock, c202=[("OWNER", "Already delivered by merged PR #302.")])
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"repository": {"p302": _merged_pull_node(302)}}},
    )

    [change] = await list_spec_changes("owner/repo")

    assert change["derived"]["status"] == "done"
    assert change["derived"]["signals"]["closed_without_merge"] is False
    grouped = group_requirements(
        [Candidate("owner/repo", change, change["derived"]["linked_issues"])],
        now=_NOW,
        stale_after_days=14,
    )
    assert _group_of(grouped, spec_change=152) == "landed"


# ---------------------------------------------------------------------------
# Activity and stalling are visible
# ---------------------------------------------------------------------------


def test_blocked_behind_stalled_work() -> None:
    quiet = (_NOW - timedelta(days=20)).isoformat()

    def unit(req_id: str, blockers: list[dict[str, Any]]) -> Candidate:
        derived = derive("in-progress", [], {}, on_pr_branch=False)
        derived.update(
            {
                "status": "in-progress",
                "source": "issues",
                "last_activity": quiet,
                "linked_issues": [int(req_id)],
                "blocked_by": blockers,
            }
        )
        derived["signals"]["unlinked"] = None
        entry = {"id": req_id, "title": req_id, "status": "in-progress", "derived": derived}
        return Candidate("owner/repo", entry, [int(req_id)])

    a = unit("1", [{"repo": "owner/repo", "number": 2, "last_activity": quiet}])
    b = unit("2", [])

    grouped = group_requirements([a, b], now=_NOW, stale_after_days=14)

    assert [entry["id"] for entry in grouped["stalled"]] == ["1", "2"]


def test_a_spec_change_moves_when_its_pull_request_does() -> None:
    facts = {42: _facts(42)}

    derived = derive_spec_change("open", "2026-09-01T00:00:00Z", [42], facts)

    assert derived["last_activity"] == "2026-09-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Silent failures are named
# ---------------------------------------------------------------------------


async def test_merged_spec_change_with_no_issues(httpx_mock: HTTPXMock) -> None:
    _mock_uses_specs(httpx_mock)
    httpx_mock.add_response(json=[_spec_change_item(158, merged_at=_stamp(10))])

    [change] = await list_spec_changes("owner/repo")

    assert change["derived"]["source"] == "unlinked"
    assert change["derived"]["signals"]["unlinked"] == "never_linked"
    assert change["warning"] == "no issues name this spec change"
    grouped = group_requirements(
        [Candidate("owner/repo", change, [])], now=_NOW, stale_after_days=14
    )
    assert _group_of(grouped, spec_change=158) == "never_linked"


async def test_an_open_spec_change_with_no_issues_awaits_its_own_pull_request(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_uses_specs(httpx_mock)
    httpx_mock.add_response(json=[_spec_change_item(162, state="open")])

    [change] = await list_spec_changes("owner/repo")

    assert change["derived"]["signals"]["unlinked"] == "awaiting_pr"
    assert change["pr"]["number"] == 162
    assert change["warning"] == "awaiting merge of PR #162"
    grouped = group_requirements(
        [Candidate("owner/repo", change, [])], now=_NOW, stale_after_days=14
    )
    assert _group_of(grouped, spec_change=162) == "awaiting_merge"


# ---------------------------------------------------------------------------
# Spec gaps are visible per capability
# ---------------------------------------------------------------------------


async def test_audit_results(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json=[
            _issue_item(n, body=_reference(None, "backlog"), labels=("spec-gap",))
            for n in (1, 2, 3)
        ]
        + [
            _issue_item(4, body=_reference(None, "backlog"), labels=("spec-gap",), state="closed"),
            _issue_item(5, body=_reference(None, "issues"), labels=("spec-gap",)),
        ]
    )

    progress = await read_spec_progress("owner/repo")

    assert progress.spec_gaps == {"backlog": 3, "issues": 1}


async def test_progress_keeps_the_reference_of_each_issue_naming_a_spec_change(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        json=[
            _issue_item(1, body=_reference(20, "milestones") + "\nRequirement: Review"),
            _issue_item(2, body=_reference(None, "backlog"), labels=("spec-gap",)),
        ]
    )

    progress = await read_spec_progress("owner/repo")

    assert progress.references == {1: SpecReference(20, ("milestones",), "Review")}


async def test_every_page_of_issues_is_read(httpx_mock: HTTPXMock) -> None:
    first_page = [_issue_item(n, body="No reference.") for n in range(1099, 999, -1)]
    second_page = [_issue_item(7, body=_reference(None), labels=("spec-gap",))]

    def page(request: httpx.Request) -> httpx.Response:
        # The newest number bounds the pages fetched at once; those past the end are empty.
        number = request.url.params["page"]
        return httpx.Response(
            200, json=first_page if number == "1" else second_page if number == "2" else []
        )

    httpx_mock.add_callback(page, url=_ISSUES_URL, is_reusable=True)

    progress = await read_spec_progress("owner/repo")

    assert progress.spec_gaps == {"backlog": 1}
    pages = {request.url.params["page"] for request in httpx_mock.get_requests(url=_ISSUES_URL)}
    assert {"1", "2"} <= pages


async def test_a_spec_change_whose_issues_cannot_be_read_is_unreadable(
    httpx_mock: HTTPXMock, signed_in: None
) -> None:
    with (
        patch("tools.requirements.db.list_repos", return_value=[{"repo_name": "owner/specs"}]),
        patch("tools.specs.uses_specs", AsyncMock(return_value=True)),
    ):
        httpx_mock.add_response(json=[])  # no requirement files
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        httpx_mock.add_response(
            json=[
                _issue_item(21, body=_reference(20)),
                _spec_change_item(20, merged_at=_stamp(3), repo="owner/specs"),
            ]
        )
        httpx_mock.add_response(method="POST", url=_GRAPHQL_URL, status_code=500, json={})
        _mock_waiting(httpx_mock)

        result = await get_dashboard()

    assert _group_of(result["groups"], spec_change=20) == "unreadable"
    assert result["summary"]["spec_changes_by_status"] == {"unreadable": 1}
    assert result["summary"]["unavailable_repos"] == [
        {"repo": "owner/specs", "count": 1, "causes": ["transient"]}
    ]


async def test_list_spec_changes_offers_every_spec_change_for_landing(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_uses_specs(httpx_mock)
    pull = _spec_change_item(152, merged_at=_stamp(5))
    pull["body"] = "Spec changes notify when they land."
    httpx_mock.add_response(json=[_issue_item(201, body=_reference(152), state="closed"), pull])
    _mock_issue_activity(httpx_mock, _issue_node(201, state="CLOSED", merged_pr=301))

    with patch("tools.specs.landing.notify_landed", new_callable=AsyncMock) as notify_landed:
        [change] = await list_spec_changes("owner/repo")

    repo, candidates = notify_landed.await_args.args
    assert repo == "owner/repo"
    assert [(c.description, c.entry) for c in candidates] == [
        ("Spec changes notify when they land.", change)
    ]


async def test_get_dashboard_offers_spec_changes_for_landing(
    httpx_mock: HTTPXMock, signed_in: None
) -> None:
    with (
        patch("tools.requirements.db.list_repos", return_value=[{"repo_name": "owner/specs"}]),
        patch("tools.specs.uses_specs", AsyncMock(return_value=True)),
        patch("tools.requirements.landing.notify_landed", new_callable=AsyncMock) as notify_landed,
    ):
        httpx_mock.add_response(json=[])  # no requirement files
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        httpx_mock.add_response(
            json=[
                _issue_item(21, body=_reference(20)),
                _spec_change_item(20, merged_at=_stamp(3), repo="owner/specs"),
            ]
        )
        _mock_issue_activity(httpx_mock, _issue_node(21, state="CLOSED", merged_pr=31))
        _mock_waiting(httpx_mock)

        result = await get_dashboard()

    assert [c["spec_change"] for c in result["repos"][0]["spec_changes"]] == [20]
    assert result["repos"][0]["scenario_results"]["capabilities"] == {
        "backlog": {"passed": ["Ordered"], "xfail": [], "failed": [], "untested": []}
    }
    repo, candidates = notify_landed.await_args.args
    assert repo == "owner/specs"
    assert [c.entry["spec_change"] for c in candidates] == [20]


@pytest.mark.parametrize("scenario_results", [None])
async def test_the_dashboard_says_when_no_scenario_results_are_published(
    httpx_mock: HTTPXMock, signed_in: None
) -> None:
    with (
        patch("tools.requirements.db.list_repos", return_value=[{"repo_name": "owner/specs"}]),
        patch("tools.specs.uses_specs", AsyncMock(return_value=True)),
    ):
        httpx_mock.add_response(json=[])  # no requirement files
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        httpx_mock.add_response(json=[])  # no issues or spec changes
        _mock_waiting(httpx_mock)

        result = await get_dashboard()

    view = result["repos"][0]["scenario_results"]
    assert view["published"] is False
    assert "scenario-results" in view["message"]


@pytest.mark.parametrize("scenario_results", [403])
async def test_unreadable_scenario_results_cost_only_the_built_spec_changes(
    httpx_mock: HTTPXMock, signed_in: None
) -> None:
    with (
        patch("tools.requirements.db.list_repos", return_value=[{"repo_name": "owner/specs"}]),
        patch("tools.specs.uses_specs", AsyncMock(return_value=True)),
        patch("tools.requirements.landing.notify_landed", new_callable=AsyncMock),
    ):
        httpx_mock.add_response(json=[])  # no requirement files
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        httpx_mock.add_response(
            json=[
                _issue_item(23, body=_reference(22)),
                _spec_change_item(22, merged_at=_stamp(2), repo="owner/specs"),
                _issue_item(21, body=_reference(20)),
                _spec_change_item(20, merged_at=_stamp(3), repo="owner/specs"),
            ]
        )
        _mock_issue_activity(
            httpx_mock, _issue_node(21, state="CLOSED", merged_pr=31), _issue_node(23)
        )
        _mock_waiting(httpx_mock)

        result = await get_dashboard()

    summary = result["summary"]
    assert summary["incomplete_repos"] == []
    assert summary["lower_bound"] is False
    assert summary["spec_changes_by_status"] == {"unreadable": 1, "idea": 1}
    assert summary["unavailable_repos"] == [
        {"repo": "owner/specs", "count": 1, "causes": ["inaccessible"]}
    ]
    view = result["repos"][0]["scenario_results"]
    assert view["published"] is False
    assert view["unavailable_cause"] == "inaccessible"


async def test_list_spec_changes_is_refused_without_specs(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/contents/openspec/specs",
        status_code=404,
        json={"message": "Not Found"},
    )

    with pytest.raises(ValueError, match="list_requirements"):
        await list_spec_changes("owner/repo")


# ---------------------------------------------------------------------------
# Questions waiting on the author come first
# ---------------------------------------------------------------------------


async def test_a_question_overnight(httpx_mock: HTTPXMock, signed_in: None) -> None:
    now = datetime.now(UTC)
    marked = (now - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    question = "Does a parked issue keep its backlog position?"
    with (
        patch("tools.requirements.db.list_repos", return_value=[{"repo_name": "owner/repo"}]),
        patch("tools.specs.uses_specs", AsyncMock(return_value=False)),
    ):
        httpx_mock.add_response(json=[])  # no requirement files
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        _mock_waiting(
            httpx_mock,
            _waiting_node(
                160,
                labelled_at=marked,
                comments=((_stamp(2), "Picked this up."), (marked, question)),
            ),
        )

        result = await get_dashboard()

    [waiting] = result["waiting_on_author"]
    assert waiting == {
        "repo": "owner/repo",
        "number": 160,
        "title": "Issue 160",
        "url": "https://github.com/owner/repo/issues/160",
        "question": question,
        "waiting_since": marked,
    }
    assert (now - datetime.fromisoformat(waiting["waiting_since"])) // timedelta(hours=1) == 6
    assert result["summary"]["waiting_on_author_count"] == 1


async def test_the_question_is_the_comment_posted_when_the_issue_was_marked(
    httpx_mock: HTTPXMock, signed_in: None
) -> None:
    with (
        patch("tools.requirements.db.list_repos", return_value=[{"repo_name": "owner/repo"}]),
        patch("tools.specs.uses_specs", AsyncMock(return_value=False)),
    ):
        httpx_mock.add_response(json=[])  # no requirement files
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        _mock_waiting(
            httpx_mock,
            _waiting_node(
                1,
                labelled_at=_stamp(3),
                comments=((_stamp(4), "Earlier."), (_stamp(3), "The question"), (_stamp(1), "+1")),
            ),
            _waiting_node(2, labelled_at=_stamp(5), comments=((_stamp(2), "Only after."),)),
            _waiting_node(3, labelled_at=_stamp(4), comments=()),
        )

        result = await get_dashboard()

    assert [(entry["number"], entry["question"]) for entry in result["waiting_on_author"]] == [
        (2, "Only after."),
        (3, None),
        (1, "The question"),
    ]


async def test_a_repository_whose_waiting_questions_cannot_be_searched_is_incomplete(
    httpx_mock: HTTPXMock, signed_in: None
) -> None:
    with (
        patch("tools.requirements.db.list_repos", return_value=[{"repo_name": "owner/repo"}]),
        patch("tools.specs.uses_specs", AsyncMock(return_value=False)),
    ):
        httpx_mock.add_response(json=[])  # no requirement files
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        httpx_mock.add_response(
            method="POST", url=_GRAPHQL_URL, json={"errors": [{"type": "FORBIDDEN"}]}
        )

        result = await get_dashboard()

    assert result["waiting_on_author"] == []
    assert result["summary"]["incomplete_repos"] == ["owner/repo"]
    assert result["summary"]["lower_bound"] is True


# ---------------------------------------------------------------------------
# One call answers the question
# ---------------------------------------------------------------------------

_REQUIREMENT_MD = """\
---
id: "001"
title: "User authentication"
status: "in-progress"
github_issue: 42
created: "2026-05-11"
updated: "2026-05-11"
---

## Why

Users need to save their rankings.
"""


def _file_content(content: str) -> dict[str, Any]:
    return {
        "name": "001-user-auth.md",
        "path": "requirements/001-user-auth.md",
        "content": base64.b64encode(content.encode()).decode("ascii"),
        "sha": "sha1",
        "type": "file",
    }


def _mock_requirement_repo(httpx_mock: HTTPXMock, node: dict) -> None:
    httpx_mock.add_response(
        json=[{"name": "001-user-auth.md", "path": "requirements/001-user-auth.md"}]
    )
    httpx_mock.add_response(json=_file_content(_REQUIREMENT_MD))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
    _mock_issue_activity(httpx_mock, node)


async def test_a_pull_request_linked_in_place_of_an_issue(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json=[{"name": "001-user-auth.md", "path": "requirements/001-user-auth.md"}]
    )
    httpx_mock.add_response(json=_file_content(_REQUIREMENT_MD.replace("42", "21")))
    httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={
            "data": {"repository": {"i21": None}},
            "errors": [{"type": "NOT_FOUND", "path": ["repository", "i21"]}],
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"repository": {"p21": _merged_pull_node(21)}}},
    )

    [requirement] = await list_requirements("owner/repo")

    assert requirement["derived"]["status"] == "done"


async def test_a_refused_check_status_does_not_stop_the_later_batches(
    httpx_mock: HTTPXMock,
) -> None:
    """A token that may not read commit statuses is refused one field in the first batch;
    the issues in the batches after it are still read (#249)."""
    from github import _ISSUE_ACTIVITY_CHUNK, get_issue_activity

    first = list(range(1, _ISSUE_ACTIVITY_CHUNK + 1))
    later = _ISSUE_ACTIVITY_CHUNK + 1
    refused = {
        "type": "FORBIDDEN",
        "path": [
            "repository",
            "i1",
            "closedByPullRequestsReferences",
            "nodes",
            0,
            "commits",
            "nodes",
            0,
            "commit",
            "statusCheckRollup",
        ],
        "message": "Resource not accessible by personal access token",
    }
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={
            "data": {"repository": {f"i{n}": _issue_node(n, state="OPEN") for n in first}},
            "errors": [refused],
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"repository": {f"i{later}": _issue_node(later, state="OPEN")}}},
    )

    activity = await get_issue_activity("owner/repo", [*first, later])

    assert activity["failure"] is None
    assert later in activity["facts"]


async def test_a_refused_status_context_reads_as_a_null_node(httpx_mock: HTTPXMock) -> None:
    """GitHub nulls a status context the token may not read inside the contexts list;
    the issue is still read rather than the whole repo going unreadable."""
    from github import get_issue_activity

    node = _issue_node(1, state="CLOSED", merged_pr=2)
    pull = node["closedByPullRequestsReferences"]["nodes"][0]
    pull["commits"] = {
        "nodes": [
            {
                "commit": {
                    "statusCheckRollup": {
                        "contexts": {
                            "nodes": [
                                None,
                                {
                                    "__typename": "CheckRun",
                                    "conclusion": "SUCCESS",
                                    "completedAt": _stamp(2),
                                },
                            ]
                        }
                    }
                }
            },
        ]
    }
    node["timelineItems"] = {"nodes": [None]}
    refused = {
        "type": "FORBIDDEN",
        "path": [
            "repository",
            "i1",
            "closedByPullRequestsReferences",
            "nodes",
            0,
            "commits",
            "nodes",
            0,
            "commit",
            "statusCheckRollup",
            "contexts",
            "nodes",
            0,
            "state",
        ],
        "message": "Resource not accessible by personal access token",
    }
    httpx_mock.add_response(
        method="POST",
        url=_GRAPHQL_URL,
        json={"data": {"repository": {"i1": node}}, "errors": [refused]},
    )

    activity = await get_issue_activity("owner/repo", [1])

    assert activity["failure"] is None
    assert [check["conclusion"] for check in activity["facts"][1]["pulls"][0]["checks"]] == [
        "SUCCESS"
    ]


async def test_morning_check(httpx_mock: HTTPXMock, signed_in: None) -> None:
    repos = [{"repo_name": "owner/specs"}, {"repo_name": "owner/quiet"}, {"repo_name": "owner/app"}]
    with (
        patch("tools.requirements.db.list_repos", return_value=repos),
        patch(
            "tools.specs.uses_specs",
            AsyncMock(side_effect=lambda repo: repo == "owner/specs"),
        ),
    ):
        httpx_mock.add_response(json=[])  # owner/specs has no requirement files
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        httpx_mock.add_response(
            json=[
                _issue_item(11, body=_reference(None, "backlog"), labels=("spec-gap",)),
                _issue_item(12, body=_reference(None, "backlog"), labels=("spec-gap",)),
                _issue_item(13, body=_reference(None, "issues"), labels=("spec-gap",)),
                _issue_item(21, body=_reference(20), state="closed"),
                _spec_change_item(20, merged_at=_stamp(3), repo="owner/specs"),
                _spec_change_item(30, state="closed", repo="owner/specs"),
            ]
        )
        _mock_issue_activity(httpx_mock, _issue_node(21, state="CLOSED", merged_pr=22))

        stalled = _issue_node(42)
        stalled["updatedAt"] = _stamp(40)
        _mock_requirement_repo(httpx_mock, stalled)
        _mock_requirement_repo(httpx_mock, _issue_node(42, state="CLOSED"))
        _mock_comments(httpx_mock, c42=[])

        question = "Should a parked issue keep its position?"
        _mock_waiting(
            httpx_mock, _waiting_node(160, labelled_at=_stamp(1), comments=((_stamp(1), question),))
        )
        _mock_waiting(httpx_mock)
        _mock_waiting(
            httpx_mock, _waiting_node(7, labelled_at=_stamp(3), comments=((_stamp(3), "Which?"),))
        )

        result = await get_dashboard()

    grouped = result["groups"]
    assert _group_of(grouped, repo="owner/specs", spec_change=20) == "landed"
    assert _group_of(grouped, repo="owner/specs", spec_change=30) == "dropped"
    assert _group_of(grouped, repo="owner/quiet", id="001") == "stalled"
    assert _group_of(grouped, repo="owner/app", id="001") == "needs_attention"

    assert [(entry["repo"], entry["number"]) for entry in result["waiting_on_author"]] == [
        ("owner/app", 7),
        ("owner/specs", 160),
    ]
    assert result["waiting_on_author"][1]["question"] == question

    summary = result["summary"]
    assert summary["waiting_on_author_count"] == 2
    assert summary["spec_gaps"] == {"owner/specs": {"backlog": 2, "issues": 1}}
    assert summary["spec_changes_by_status"] == {"done": 1, "none": 1}
    assert summary["total"] == sum(summary["groups"].values()) == 4
    assert summary["lower_bound"] is False

    listed = {entry["repo"]: entry for entry in result["repos"]}
    assert [c["spec_change"] for c in listed["owner/specs"]["spec_changes"]] == [20]
    assert listed["owner/quiet"]["spec_changes"] == []


@pytest.mark.parametrize(("status", "listed"), [("none", [158]), ("unreadable", [20])])
async def test_the_dashboard_filters_on_a_spec_change_only_status(
    httpx_mock: HTTPXMock, signed_in: None, status: str, listed: list[int]
) -> None:
    with (
        patch("tools.requirements.db.list_repos", return_value=[{"repo_name": "owner/specs"}]),
        patch("tools.specs.uses_specs", AsyncMock(return_value=True)),
    ):
        httpx_mock.add_response(json=[])  # no requirement files
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        httpx_mock.add_response(
            json=[
                _issue_item(21, body=_reference(20)),
                _spec_change_item(20, merged_at=_stamp(3), repo="owner/specs"),
                _spec_change_item(158, merged_at=_stamp(10), repo="owner/specs"),
            ]
        )
        httpx_mock.add_response(method="POST", url=_GRAPHQL_URL, status_code=500, json={})
        _mock_waiting(httpx_mock)

        result = await get_dashboard(status=status)

    [repo] = result["repos"]
    assert [change["spec_change"] for change in repo["spec_changes"]] == listed


async def test_an_unknown_dashboard_status_names_the_spec_change_statuses_too(
    signed_in: None,
) -> None:
    with pytest.raises(ValueError, match="'unreadable'"):
        await get_dashboard(status="bogus")


async def test_a_spec_repository_whose_spec_changes_cannot_be_read_is_incomplete(
    httpx_mock: HTTPXMock, signed_in: None
) -> None:
    with (
        patch("tools.requirements.db.list_repos", return_value=[{"repo_name": "owner/specs"}]),
        patch("tools.specs.uses_specs", AsyncMock(return_value=True)),
    ):
        httpx_mock.add_response(json=[])  # no requirement files
        httpx_mock.add_response(json=[])  # list_lachesis_prs → no PRs
        httpx_mock.add_response(status_code=502)
        _mock_waiting(httpx_mock)

        result = await get_dashboard()

    assert [entry["repo"] for entry in result["repos"]] == ["owner/specs"]
    assert result["summary"]["incomplete_repos"] == ["owner/specs"]
    assert result["summary"]["lower_bound"] is True
    assert result["summary"]["spec_gaps"] == {}


# ---------------------------------------------------------------------------
# Partial results are honest
# ---------------------------------------------------------------------------


async def test_rate_limited_on_one_repository(httpx_mock: HTTPXMock, signed_in: None) -> None:
    with (
        patch(
            "tools.requirements.db.list_repos",
            return_value=[{"repo_name": "owner/first"}, {"repo_name": "owner/second"}],
        ),
        patch("tools.specs.uses_specs", AsyncMock(return_value=False)),
    ):
        httpx_mock.add_response(
            status_code=403,
            headers={"x-ratelimit-remaining": "0"},
            json={"message": "API rate limit exceeded"},
        )
        _mock_requirement_repo(httpx_mock, _issue_node(42))
        _mock_waiting(httpx_mock)
        result = await get_dashboard()

    assert result["summary"]["failed_repos"] == [{"repo": "owner/first", "cause": "rate_limited"}]
    assert result["summary"]["lower_bound"] is True


# ---------------------------------------------------------------------------
# A web view shows the same picture
# ---------------------------------------------------------------------------


async def test_signed_out_visit(monkeypatch: pytest.MonkeyPatch) -> None:
    from main import app

    list_repos = MagicMock()
    monkeypatch.setattr("web.db.list_repos", list_repos)

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        resp = await client.get("/web/")

    assert resp.status_code == 302
    assert resp.headers["location"] == "/web/login"
    list_repos.assert_not_called()


async def test_the_web_view_lists_spec_changes_with_status_and_issue_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jose import jwt as jose_jwt

    from main import app
    from tools.specs import SpecProgress
    from web import _COOKIE_NAME

    monkeypatch.setattr("web.settings.secret_key", "test-secret")
    monkeypatch.setattr("web.settings.allowed_email", "user@example.com")
    monkeypatch.setattr("web.db.list_repos", MagicMock(return_value=[{"repo_name": "owner/specs"}]))
    monkeypatch.setattr("web.github.list_files", AsyncMock(return_value=[]))
    monkeypatch.setattr("web.uses_specs", AsyncMock(return_value=True))
    monkeypatch.setattr("web.github.list_waiting_issues", AsyncMock(return_value=[]))
    monkeypatch.setattr("web.db.list_open_questions", MagicMock(return_value=[]))
    built = derive_spec_change(
        "merged", None, [1, 2, 3], {n: _facts(n, state="CLOSED", merged=True) for n in (1, 2, 3)}
    )
    change = {
        "spec_change": 152,
        "title": "Progress reporting on spec changes",
        "url": "https://github.com/owner/specs/pull/152",
        "dropped": False,
        "derived": built,
    }
    monkeypatch.setattr(
        "web.read_spec_progress",
        AsyncMock(
            return_value=SpecProgress({"backlog": 3}, [SpecChangeCandidate("", change)], {}, {})
        ),
    )
    token = jose_jwt.encode(
        {"sub": "google-sub", "email": "user@example.com", "lachesis_uid": "uid", "exp": 2**31},
        "test-secret",
        algorithm="HS256",
    )

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        client.cookies.set(_COOKIE_NAME, token)
        resp = await client.get("/web/")

    assert resp.status_code == 200
    assert "Progress reporting on spec changes" in resp.text
    assert "done (1)" in resp.text
    assert "3 issues" in resp.text
    assert "Open spec gaps" in resp.text
    assert "3 open" in resp.text


async def _web_dashboard(
    monkeypatch: pytest.MonkeyPatch,
    list_waiting_issues: AsyncMock,
    open_questions: list[dict[str, Any]] | None = None,
    repos: list[str] | None = None,
) -> str:
    from jose import jwt as jose_jwt

    from main import app
    from web import _COOKIE_NAME

    monkeypatch.setattr("web.settings.secret_key", "test-secret")
    monkeypatch.setattr("web.settings.allowed_email", "user@example.com")
    monkeypatch.setattr(
        "web.db.list_repos",
        MagicMock(return_value=[{"repo_name": name} for name in repos or ["owner/repo"]]),
    )
    monkeypatch.setattr("web.github.list_files", AsyncMock(return_value=[]))
    monkeypatch.setattr("web.uses_specs", AsyncMock(return_value=False))
    monkeypatch.setattr("web.github.list_waiting_issues", list_waiting_issues)
    monkeypatch.setattr("web.db.list_open_questions", MagicMock(return_value=open_questions or []))
    token = jose_jwt.encode(
        {"sub": "google-sub", "email": "user@example.com", "lachesis_uid": "uid", "exp": 2**31},
        "test-secret",
        algorithm="HS256",
    )

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        client.cookies.set(_COOKIE_NAME, token)
        resp = await client.get("/web/")

    assert resp.status_code == 200
    return resp.text


async def test_the_web_view_lists_the_questions_waiting_on_the_author_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marked = (datetime.now(UTC) - timedelta(hours=6, minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    page = await _web_dashboard(
        monkeypatch,
        AsyncMock(
            return_value=[
                {
                    "number": 160,
                    "title": "Park issues",
                    "url": "https://github.com/owner/repo/issues/160",
                    "question": "Does a parked issue keep its position?",
                    "waiting_since": marked,
                }
            ]
        ),
    )

    assert "owner/repo#160" in page
    assert "Does a parked issue keep its position?" in page
    assert "waiting 6h" in page
    assert page.index("Waiting on you") < page.index("No requirements found.")


async def test_the_web_view_lists_open_questions_then_issues_no_question_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def waiting(number: int, title: str) -> dict[str, Any]:
        return {
            "number": number,
            "title": title,
            "url": f"https://github.com/owner/repo/issues/{number}",
            "question": None,
            "waiting_since": "2026-09-01T00:00:00Z",
        }

    question = {
        "id": 7,
        "repo_name": "owner/repo",
        "question": "May the backlog use priority labels?",
        "kind": "large_work",
        "capability": "backlog",
        "spec_change": None,
        "milestone": None,
        "issue": None,
        "blocks": [160],
        "created_at": datetime.now(UTC) - timedelta(hours=30),
    }
    page = await _web_dashboard(
        monkeypatch,
        AsyncMock(return_value=[waiting(160, "Park issues"), waiting(99, "Labelled by hand")]),
        open_questions=[question],
    )

    assert "Waiting on you (2)" in page
    assert "May the backlog use priority labels?" in page
    assert "large work question on backlog" in page
    assert "waiting 1d" in page
    assert "owner/repo#99" in page
    assert "Park issues" not in page
    assert page.index("May the backlog use priority labels?") < page.index("owner/repo#99")


async def test_the_web_view_keeps_an_issue_whose_number_a_question_blocks_in_another_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def waiting_in(repo: str, label: str) -> list[dict[str, Any]]:
        return [
            {
                "number": 160,
                "title": f"Park issues in {repo}",
                "url": f"https://github.com/{repo}/issues/160",
                "question": None,
                "waiting_since": "2026-09-01T00:00:00Z",
            }
        ]

    question = {
        "id": 7,
        "repo_name": "owner/repo-a",
        "question": "May the backlog use priority labels?",
        "kind": "large_work",
        "capability": "backlog",
        "spec_change": None,
        "milestone": None,
        "issue": None,
        "blocks": [160],
        "created_at": datetime.now(UTC) - timedelta(hours=30),
    }
    page = await _web_dashboard(
        monkeypatch,
        AsyncMock(side_effect=waiting_in),
        open_questions=[question],
        repos=["owner/repo-a", "owner/repo-b"],
    )

    assert "Waiting on you (2)" in page
    assert "Park issues in owner/repo-b" in page
    assert "Park issues in owner/repo-a" not in page


async def test_the_web_view_says_when_a_repository_could_not_be_searched_for_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = await _web_dashboard(monkeypatch, AsyncMock(side_effect=RuntimeError("rate limited")))

    assert "Waiting on you (0)" in page
    assert "There may be more: could not search owner/repo." in page


@pytest.mark.parametrize(
    ("elapsed", "shown"),
    [
        (timedelta(hours=23, minutes=59), "23h"),
        (timedelta(hours=24), "1d"),
        (timedelta(days=3, hours=5), "3d"),
    ],
)
def test_the_web_view_shows_waits_past_a_day_in_days(elapsed: timedelta, shown: str) -> None:
    from web import _waited

    now = datetime(2026, 9, 27, 12, tzinfo=UTC)
    assert _waited((now - elapsed).isoformat(), now) == shown
