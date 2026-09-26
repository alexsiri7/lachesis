"""The progress-reporting capability's scenarios, one test named after each.

See ``openspec/specs/progress-reporting/spec.md``.
"""

from __future__ import annotations

import base64
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
from tools.requirements import get_dashboard
from tools.specs import list_spec_changes, read_spec_progress

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


def _issue_node(number: int, *, state: str = "OPEN", merged_pr: int | None = None) -> dict:
    """A GraphQL issue node as the issue-activity query returns it."""
    pulls = []
    if merged_pr is not None:
        pulls.append(
            {
                "number": merged_pr,
                "state": "MERGED",
                "merged": True,
                "updatedAt": _stamp(2),
                "mergedAt": _stamp(2),
                "isDraft": False,
                "commits": {"nodes": []},
            }
        )
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
    httpx_mock.add_response(json=[_spec_change_item(160, state="closed")])

    assert await list_spec_changes("owner/repo") == []

    _mock_uses_specs(httpx_mock)
    httpx_mock.add_response(json=[_spec_change_item(160, state="closed")])
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

    [change] = await list_spec_changes("owner/repo")

    assert change["dropped"] is False
    grouped = group_requirements(
        [Candidate("owner/repo", change, change["derived"]["linked_issues"])],
        now=_NOW,
        stale_after_days=14,
    )
    assert _group_of(grouped, spec_change=152) == "needs_attention"


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


async def test_every_page_of_issues_is_read(httpx_mock: HTTPXMock) -> None:
    first_page = [_issue_item(n, body="No reference.") for n in range(1000, 1100)]
    httpx_mock.add_response(json=first_page)
    httpx_mock.add_response(json=[_issue_item(7, body=_reference(None), labels=("spec-gap",))])

    progress = await read_spec_progress("owner/repo")

    assert progress.spec_gaps == {"backlog": 1}
    pages = [request.url.params["page"] for request in httpx_mock.get_requests()]
    assert pages == ["1", "2"]


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

        result = await get_dashboard()

    assert [c["spec_change"] for c in result["repos"][0]["spec_changes"]] == [20]
    repo, candidates = notify_landed.await_args.args
    assert repo == "owner/specs"
    assert [c.entry["spec_change"] for c in candidates] == [20]


async def test_list_spec_changes_is_refused_without_specs(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/contents/openspec/specs",
        status_code=404,
        json={"message": "Not Found"},
    )

    with pytest.raises(ValueError, match="list_requirements"):
        await list_spec_changes("owner/repo")


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

        result = await get_dashboard()

    grouped = result["groups"]
    assert _group_of(grouped, repo="owner/specs", spec_change=20) == "landed"
    assert _group_of(grouped, repo="owner/specs", spec_change=30) == "dropped"
    assert _group_of(grouped, repo="owner/quiet", id="001") == "stalled"
    assert _group_of(grouped, repo="owner/app", id="001") == "needs_attention"

    summary = result["summary"]
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
        AsyncMock(return_value=SpecProgress([change], {"backlog": 3}, [])),
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
