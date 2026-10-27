"""Progress read from GitHub: derived status, dropped and stalled work, silent failures,
spec gaps, questions waiting on the author, partial results and the signed-out web view.

A repository without ``openspec/specs`` tracks its work in requirement files; one with it
tracks spec changes, the pull requests labelled ``spec-change``.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from harness import FakeGitHub, Lachesis, scenario

REPO = "owner/repo"
NOW = datetime.now(UTC)

# The GraphQL queries Lachesis sends, told apart by text only each one carries.
ACTIVITY = "fragment activity"
COMMENTS = "comments(last: 20)"
PULLS = "pullRequest(number:"
WAITING = "LABELED_EVENT"


def _stamp(*, days: float = 0, hours: float = 0) -> str:
    return (NOW - timedelta(days=days, hours=hours)).isoformat()


async def _register(lachesis: Lachesis, repo: str = REPO) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": repo})
    assert not registered.is_error, registered.text


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


def _requirement(
    id_: str, status: str, issues: list[int], *, title: str | None = None
) -> tuple[str, str]:
    """Requirement file *id_* recorded as *status*, linking *issues*: its path and content."""
    title = title or f"Requirement {id_}"
    bullets = "\n".join(f"- #{number}" for number in issues)
    content = (
        f"---\nid: '{id_}'\ntitle: {title}\nstatus: {status}\n---\n\n"
        f"## What\n\n{title}.\n\n## Issues\n\n{bullets}\n"
    )
    return f"requirements/{id_}-requirement.md", content


def _requirement_files(github: FakeGitHub, repo: str, *files: tuple[str, str]) -> None:
    """*files* are every requirement on *repo*'s default branch; no pull request is open."""
    github.on(
        "GET",
        f"/repos/{repo}/contents/requirements",
        json=[{"type": "file", "name": path.rsplit("/", 1)[-1], "path": path} for path, _ in files],
    )
    for path, content in files:
        github.on(
            "GET",
            f"/repos/{repo}/contents/{path}",
            json={
                "type": "file",
                "path": path,
                "sha": "f11e",
                "encoding": "base64",
                "content": base64.b64encode(content.encode()).decode(),
            },
        )
    github.on("GET", f"/repos/{repo}/pulls", json=[])


def _pull(number: int, *, merged: bool, days: float = 1) -> dict[str, Any]:
    """Pull request *number* as GitHub's GraphQL API returns it."""
    return {
        "number": number,
        "state": "MERGED" if merged else "CLOSED",
        "merged": merged,
        "updatedAt": _stamp(days=days),
        "mergedAt": _stamp(days=days) if merged else None,
        "mergeCommit": {"oid": f"c0mm1t{number}"} if merged else None,
        "isDraft": False,
        "commits": {"nodes": []},
    }


def _issue_node(
    number: int,
    *,
    closed: bool,
    merged_pull: int | None = None,
    days: float = 1,
    blocked_by: tuple[tuple[int, float], ...] = (),
) -> dict[str, Any]:
    """Issue *number* as the issue-activity query returns it, last moved *days* ago.

    *blocked_by* holds each open issue blocking it, with how many days ago it last moved.
    """
    return {
        "number": number,
        "state": "CLOSED" if closed else "OPEN",
        "updatedAt": _stamp(days=days),
        "closedAt": _stamp(days=days) if closed else None,
        "labels": {"nodes": []},
        "blockedBy": {
            "nodes": [
                {
                    "number": blocker,
                    "state": "OPEN",
                    "updatedAt": _stamp(days=quiet),
                    "repository": {"nameWithOwner": REPO},
                }
                for blocker, quiet in blocked_by
            ]
        },
        "timelineItems": {"nodes": []},
        "closedByPullRequestsReferences": {
            "nodes": [] if merged_pull is None else [_pull(merged_pull, merged=True, days=days)]
        },
    }


def _graphql(github: FakeGitHub, repo: str, query: str, aliases: dict[str, Any]) -> None:
    github.on_graphql(repo, query, json={"data": {"repository": aliases}})


def _activity(github: FakeGitHub, repo: str, *nodes: dict[str, Any]) -> None:
    _graphql(github, repo, ACTIVITY, {f"i{node['number']}": node for node in nodes})


def _waiting(github: FakeGitHub, repo: str, *issues: dict[str, Any]) -> None:
    """The open issues on *repo* labelled ``needs-author``."""
    _graphql(github, repo, WAITING, {"issues": {"nodes": list(issues)}})


def _question(number: int, question: str, *, asked_hours_ago: float) -> dict[str, Any]:
    """Issue *number*, labelled ``needs-author`` just after *question* was posted on it."""
    labelled = _stamp(hours=asked_hours_ago)
    return {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://github.com/{REPO}/issues/{number}",
        "createdAt": _stamp(days=3),
        "timelineItems": {"nodes": [{"createdAt": labelled, "label": {"name": "needs-author"}}]},
        "comments": {
            "nodes": [
                {"body": "Started on it.", "createdAt": _stamp(days=2)},
                {"body": question, "createdAt": _stamp(hours=asked_hours_ago, days=1 / 1440)},
            ]
        },
    }


def _listed_issue(
    repo: str,
    number: int,
    *,
    state: str = "open",
    labels: tuple[str, ...] = (),
    body: str = "",
    days: float = 1,
) -> dict[str, Any]:
    """Issue *number* as the REST /issues listing returns it."""
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": body,
        "state": state,
        "labels": [{"name": label} for label in labels],
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "created_at": _stamp(days=days + 1),
        "updated_at": _stamp(days=days),
        "closed_at": _stamp(days=days) if state == "closed" else None,
        "state_reason": "completed" if state == "closed" else None,
    }


def _spec_change_pull(
    repo: str, number: int, *, merged: bool, state: str = "closed", days: float = 1
) -> dict[str, Any]:
    """Spec change *number*, a pull request labelled ``spec-change``, in the /issues listing."""
    return {
        **_listed_issue(repo, number, state=state, labels=("spec-change",), days=days),
        "pull_request": {
            "html_url": f"https://github.com/{repo}/pull/{number}",
            "merged_at": _stamp(days=days) if merged else None,
        },
    }


def _uses_specs(github: FakeGitHub, repo: str, *items: dict[str, Any]) -> None:
    """*repo* has a widgets capability, and *items* are all its issues and pull requests."""
    github.on(
        "GET",
        f"/repos/{repo}/contents/openspec/specs",
        json=[{"type": "dir", "name": "widgets", "path": "openspec/specs/widgets"}],
    )
    github.on("GET", f"/repos/{repo}/issues", json=list(items))
    github.on("GET", f"/repos/{repo}/pulls", json=[])


def _publish(github: FakeGitHub, repo: str, results: dict[str, dict[str, str]]) -> None:
    """The scenario results CI published for *repo*'s default branch."""
    github.on(
        "GET",
        f"/repos/{repo}/commits/main/check-runs",
        json={
            "total_count": 1,
            "check_runs": [
                {
                    "name": "scenario-results",
                    "head_sha": "c0ffee",
                    "status": "completed",
                    "conclusion": "success",
                    "completed_at": _stamp(),
                    "output": {"title": "Scenario results", "text": json.dumps(results)},
                }
            ],
        },
    )


def _spec_gap(repo: str, number: int, capability: str) -> dict[str, Any]:
    body = f"The spec says more than the code does.\n\n---\nSpec gap: yes\nCapability: {capability}"
    return _listed_issue(repo, number, labels=("spec-gap",), body=body)


def _naming(repo: str, number: int, spec_change: int, *, state: str) -> dict[str, Any]:
    body = f"Build it.\n\n---\nSpec change: #{spec_change}\nCapability: widgets"
    return _listed_issue(repo, number, state=state, body=body)


async def _list(lachesis: Lachesis) -> list[dict[str, Any]]:
    return await _ok(lachesis, "list_requirements", {"repo": REPO})  # type: ignore[no-any-return]


async def _dashboard(lachesis: Lachesis) -> dict[str, Any]:
    return await _ok(lachesis, "get_dashboard", {})  # type: ignore[no-any-return]


def _units(group: list[dict[str, Any]]) -> set[tuple[str, str | int]]:
    """Each unit of work in *group* by repository, and spec change number or requirement id."""
    return {(unit["repo"], unit.get("spec_change", unit.get("id"))) for unit in group}


@scenario("progress-reporting", "Requirement file shipped but never updated")
async def test_requirement_file_shipped_but_never_updated(lachesis: Lachesis) -> None:
    await _register(lachesis)
    _requirement_files(lachesis.github, REPO, _requirement("004", "idea", [11, 12]))
    _activity(
        lachesis.github,
        REPO,
        _issue_node(11, closed=True, merged_pull=111),
        _issue_node(12, closed=True, merged_pull=112),
    )

    [requirement] = await _list(lachesis)

    assert requirement["derived"]["status"] == "done"
    assert requirement["derived"]["source"] == "issues"


@scenario("progress-reporting", "Diverged requirement file")
async def test_diverged_requirement_file(lachesis: Lachesis) -> None:
    await _register(lachesis)
    _requirement_files(lachesis.github, REPO, _requirement("004", "idea", [11]))
    _activity(lachesis.github, REPO, _issue_node(11, closed=True, merged_pull=111))

    [requirement] = await _list(lachesis)

    assert requirement["status"] == "idea"
    assert requirement["derived"]["status"] == "done"
    assert requirement["derived"]["diverged"] is True


@scenario("progress-reporting", "Spec change abandoned")
async def test_spec_change_abandoned(lachesis: Lachesis) -> None:
    await _register(lachesis)
    _uses_specs(lachesis.github, REPO, _spec_change_pull(REPO, 40, merged=False))

    dashboard = await _dashboard(lachesis)

    assert _units(dashboard["groups"]["dropped"]) == {(REPO, 40)}
    assert dashboard["summary"]["groups"]["dropped"] == 1


@scenario("progress-reporting", "Blocked behind stalled work")
async def test_blocked_behind_stalled_work(lachesis: Lachesis) -> None:
    await _register(lachesis)
    _requirement_files(
        lachesis.github,
        REPO,
        _requirement("001", "idea", [1], title="Unit A"),
        _requirement("002", "idea", [2], title="Unit B"),
    )
    _activity(
        lachesis.github,
        REPO,
        _issue_node(1, closed=False, days=20, blocked_by=((2, 20),)),
        _issue_node(2, closed=False, days=20),
    )
    _waiting(lachesis.github, REPO)

    dashboard = await _dashboard(lachesis)

    assert dashboard["summary"]["stale_after_days"] == 14
    assert _units(dashboard["groups"]["stalled"]) == {(REPO, "001"), (REPO, "002")}


@scenario("progress-reporting", "Merged spec change with no issues")
async def test_merged_spec_change_with_no_issues(lachesis: Lachesis) -> None:
    await _register(lachesis)
    _uses_specs(lachesis.github, REPO, _spec_change_pull(REPO, 50, merged=True, days=10))
    _waiting(lachesis.github, REPO)

    dashboard = await _dashboard(lachesis)

    [listing] = dashboard["repos"]
    [change] = listing["spec_changes"]
    assert change["spec_change"] == 50
    assert change["derived"]["source"] == "unlinked"
    assert change["derived"]["signals"]["unlinked"] == "never_linked"
    assert _units(dashboard["groups"]["never_linked"]) == {(REPO, 50)}


@scenario("progress-reporting", "Issue closed by hand naming the merged pull request")
async def test_issue_closed_by_hand_naming_the_merged_pull_request(lachesis: Lachesis) -> None:
    await _register(lachesis)
    _requirement_files(lachesis.github, REPO, _requirement("007", "done", [370]))
    _activity(lachesis.github, REPO, _issue_node(370, closed=True))
    _graphql(
        lachesis.github,
        REPO,
        COMMENTS,
        {
            "c370": {
                "number": 370,
                "comments": {
                    "nodes": [{"body": "Delivered by #384, closing.", "authorAssociation": "OWNER"}]
                },
            }
        },
    )
    _graphql(lachesis.github, REPO, PULLS, {"p384": _pull(384, merged=True)})
    _waiting(lachesis.github, REPO)

    dashboard = await _dashboard(lachesis)

    [listing] = dashboard["repos"]
    [requirement] = listing["requirements"]
    assert requirement["derived"]["status"] == "done"
    assert requirement["derived"]["signals"]["closed_without_merge"] is False
    assert _units(dashboard["groups"]["landed"]) == {(REPO, "007")}
    assert dashboard["groups"]["needs_attention"] == []


@scenario("progress-reporting", "A pull request linked in place of an issue")
async def test_a_pull_request_linked_in_place_of_an_issue(lachesis: Lachesis) -> None:
    await _register(lachesis)
    _requirement_files(lachesis.github, REPO, _requirement("008", "in-progress", [21]))
    lachesis.github.on_graphql(
        REPO,
        ACTIVITY,
        json={
            "data": {"repository": {"i21": None}},
            "errors": [
                {
                    "type": "NOT_FOUND",
                    "path": ["repository", "i21"],
                    "message": "Could not resolve to an Issue with the number of 21.",
                }
            ],
        },
    )
    _graphql(lachesis.github, REPO, PULLS, {"p21": _pull(21, merged=True)})

    [requirement] = await _list(lachesis)

    assert requirement["derived"]["status"] == "done"


@scenario("progress-reporting", "Audit results")
async def test_audit_results(lachesis: Lachesis) -> None:
    await _register(lachesis)
    _uses_specs(
        lachesis.github,
        REPO,
        _spec_gap(REPO, 61, "backlog"),
        _spec_gap(REPO, 62, "backlog"),
        _spec_gap(REPO, 63, "backlog"),
    )
    _waiting(lachesis.github, REPO)

    dashboard = await _dashboard(lachesis)

    assert dashboard["summary"]["spec_gaps"] == {REPO: {"backlog": 3}}


@scenario("progress-reporting", "A question overnight")
async def test_a_question_overnight(lachesis: Lachesis) -> None:
    await _register(lachesis)
    _waiting(lachesis.github, REPO, _question(160, "Should widgets round up?", asked_hours_ago=6))

    dashboard = await _dashboard(lachesis)

    [waiting] = dashboard["waiting_on_author"]
    assert (waiting["repo"], waiting["number"]) == (REPO, 160)
    assert waiting["title"] == "Issue 160"
    assert waiting["question"] == "Should widgets round up?"
    waited = datetime.now(UTC) - datetime.fromisoformat(waiting["waiting_since"])
    assert round(waited / timedelta(hours=1)) == 6
    assert dashboard["summary"]["waiting_on_author_count"] == 1


@scenario("progress-reporting", "Morning check")
async def test_morning_check(lachesis: Lachesis) -> None:
    github = lachesis.github
    specs, requirements, questions = "owner/specs", "owner/requirements", "owner/questions"
    for repo in (specs, requirements, questions):
        await _register(lachesis, repo)

    _uses_specs(
        github,
        specs,
        _spec_change_pull(specs, 5, merged=True, days=3),
        _naming(specs, 6, 5, state="closed"),
        _spec_change_pull(specs, 8, merged=False),
        _spec_gap(specs, 9, "widgets"),
    )
    _activity(github, specs, _issue_node(6, closed=True, merged_pull=7))
    _publish(github, specs, {"widgets": {"Widget kept": "passed"}})
    _waiting(github, specs)

    _requirement_files(
        github,
        requirements,
        _requirement("001", "in-progress", [1]),
        _requirement("002", "idea", [2]),
    )
    _activity(
        github, requirements, _issue_node(1, closed=True), _issue_node(2, closed=False, days=30)
    )
    _graphql(github, requirements, COMMENTS, {"c1": {"number": 1, "comments": {"nodes": []}}})
    _waiting(github, requirements)

    _requirement_files(github, questions, _requirement("003", "cancelled", []))
    _waiting(github, questions, _question(12, "Which widget comes first?", asked_hours_ago=8))

    dashboard = await _dashboard(lachesis)

    groups = dashboard["groups"]
    assert set(groups) == {
        "dropped",
        "awaiting_merge",
        "landed",
        "needs_attention",
        "unreadable",
        "never_linked",
        "stalled",
        "diverged",
        "in_flight",
    }
    assert [(q["repo"], q["number"], q["question"]) for q in dashboard["waiting_on_author"]] == [
        (questions, 12, "Which widget comes first?")
    ]
    assert _units(groups["landed"]) == {(specs, 5)}
    assert _units(groups["dropped"]) == {(specs, 8), (questions, "003")}
    assert _units(groups["stalled"]) == {(requirements, "002")}
    assert _units(groups["needs_attention"]) == {(requirements, "001")}
    summary = dashboard["summary"]
    assert summary["spec_gaps"] == {specs: {"widgets": 1}}
    assert summary["waiting_on_author_count"] == 1
    assert summary["groups"] == {name: len(units) for name, units in groups.items()}
    assert summary["lower_bound"] is False


@scenario("progress-reporting", "Rate limited on one repository")
async def test_rate_limited_on_one_repository(lachesis: Lachesis) -> None:
    limited = "owner/limited"
    await _register(lachesis)
    await _register(lachesis, limited)
    _requirement_files(lachesis.github, REPO, _requirement("001", "idea", []))
    _waiting(lachesis.github, REPO)
    lachesis.github.on(
        "GET",
        f"/repos/{limited}/contents/requirements",
        json={"message": "API rate limit exceeded"},
        status=429,
    )

    summary = (await _dashboard(lachesis))["summary"]

    assert summary["failed_repos"] == [{"repo": limited, "cause": "rate_limited"}]
    assert summary["lower_bound"] is True


@scenario("progress-reporting", "Signed-out visit")
async def test_signed_out_visit(lachesis: Lachesis) -> None:
    await _register(lachesis)
    _requirement_files(
        lachesis.github, REPO, _requirement("001", "idea", [], title="Secret widget plan")
    )
    # Only the visit's own requests count: registering the repo may call GitHub.
    before_visit = len(lachesis.github.requests)

    async with httpx.AsyncClient(base_url=lachesis.base_url, follow_redirects=True) as http:
        response = await http.get("/web/")

    assert response.status_code == 200
    assert response.url.path == "/web/login"
    assert "https://accounts.google.com/" in response.text
    assert "Secret widget plan" not in response.text
    assert REPO not in response.text
    assert lachesis.github.requests[before_visit:] == []
