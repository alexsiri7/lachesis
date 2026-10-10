"""Issues: created ready and referenced, read and edited, discussed, and waiting on the author."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from harness import Lachesis, scenario

REPO = "owner/repo"
NOW = datetime.now(UTC)


def _issue(
    number: int,
    *,
    age: timedelta = timedelta(minutes=1),
    labels: tuple[str, ...] = (),
    state: str = "open",
) -> dict[str, Any]:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": state,
        "labels": [{"name": label} for label in labels],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": (NOW - age).isoformat(),
        "updated_at": (NOW - age).isoformat(),
        "closed_at": None,
        "state_reason": None,
    }


def _pull(number: int, *, state: str, merged: bool) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"Pull request {number}",
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "state": state,
        "merged": merged,
        "merged_at": None,
        "mergeable": None,
        "mergeable_state": "unknown",
        "head": {"ref": f"branch-{number}"},
        "base": {"ref": "main"},
        "draft": False,
        "labels": [],
    }


def _comment(id: int, author: str, created_at: str, body: str) -> dict[str, Any]:
    return {
        "user": {"login": author},
        "created_at": created_at,
        "body": body,
        "html_url": f"https://github.com/{REPO}/issues/160#issuecomment-{id}",
    }


async def _register(lachesis: Lachesis) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text


def _uses_specs(lachesis: Lachesis) -> None:
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "backlog", "path": "openspec/specs/backlog"}],
    )


def _holds(lachesis: Lachesis, *items: dict[str, Any]) -> None:
    lachesis.github.on("GET", f"/repos/{REPO}/issues", json=list(items))
    for item in items:
        lachesis.github.on("GET", f"/repos/{REPO}/issues/{item['number']}", json=item)


def _created(lachesis: Lachesis) -> list[dict[str, Any]]:
    return [
        request.body for request in lachesis.github.requests_to("POST", f"/repos/{REPO}/issues")
    ]


async def _create(lachesis: Lachesis, *issues: dict[str, Any]) -> list[dict[str, Any]]:
    result = await lachesis.call_tool(
        "create_github_issues", {"repo": REPO, "issues": list(issues)}
    )
    assert not result.is_error, result.text
    return result.json()


def _waits_for_answers(lachesis: Lachesis, number: int) -> None:
    github = lachesis.github
    github.on(
        "POST",
        f"/repos/{REPO}/issues/{number}/comments",
        json={"html_url": f"https://github.com/{REPO}/issues/{number}#issuecomment-1"},
        status=201,
    )
    github.on("POST", f"/repos/{REPO}/labels", json={"name": "needs-author"}, status=201)
    github.on("POST", f"/repos/{REPO}/issues/{number}/labels", json=[{"name": "needs-author"}])
    github.on("DELETE", f"/repos/{REPO}/issues/{number}/labels/needs-author", json=[])


def _comments_posted(lachesis: Lachesis, number: int) -> list[str]:
    return [
        request.body["body"]
        for request in lachesis.github.requests_to(
            "POST", f"/repos/{REPO}/issues/{number}/comments"
        )
    ]


@scenario("issues", "Batch creation")
async def test_batch_creation(lachesis: Lachesis) -> None:
    await _register(lachesis)
    backlog = [_issue(number, age=timedelta(hours=10 - number)) for number in range(1, 6)]
    _holds(lachesis, *backlog)
    lachesis.github.on_each(
        "POST",
        f"/repos/{REPO}/issues",
        json=[_issue(6), _issue(7), _issue(8)],
        status=201,
    )

    await _create(
        lachesis,
        {"title": "First", "body": "One."},
        {"title": "Second", "body": "Two.", "ready": False},
        {"title": "Third", "body": "Three."},
    )
    _holds(
        lachesis,
        *backlog,
        _issue(6, age=timedelta(seconds=3)),
        _issue(7, age=timedelta(seconds=2)),
        _issue(8, age=timedelta(seconds=1)),
    )
    result = await lachesis.call_tool("get_backlog", {"repo": REPO})

    assert not result.is_error, result.text
    entries = {entry["number"]: entry for entry in result.json()}
    assert [entries[n]["position"] for n in (6, 7, 8)] == [5, 6, 7]
    assert [entries[n]["ready"] for n in (6, 7, 8)] == [True, False, True]


@scenario("issues", "Blocked at birth")
async def test_blocked_at_birth(lachesis: Lachesis) -> None:
    github = lachesis.github
    await _register(lachesis)
    _holds(lachesis, _issue(40))
    github.on("POST", f"/repos/{REPO}/issues", json=_issue(41), status=201)
    github.on(
        "POST",
        f"/repos/{REPO}/issues/41/dependencies/blocked_by",
        json=_issue(40),
        status=201,
    )

    await _create(lachesis, {"title": "After 40", "body": "Then this.", "blocked_by": [40]})
    github.on("GET", f"/repos/{REPO}/issues/41/dependencies/blocked_by", json=[_issue(40)])
    github.on("GET", f"/repos/{REPO}/issues/41/dependencies/blocking", json=[])
    result = await lachesis.call_tool("get_dependencies", {"repo": REPO, "issue_number": 41})

    assert not result.is_error, result.text
    assert result.json()["blocked_by"] == [40]
    declared = github.requests_to("POST", f"/repos/{REPO}/issues/41/dependencies/blocked_by")
    assert [request.body["issue_id"] for request in declared] == [_issue(40)["id"]]


@scenario("issues", "No reference")
async def test_no_reference(lachesis: Lachesis) -> None:
    _uses_specs(lachesis)
    await _register(lachesis)
    lachesis.github.on("POST", f"/repos/{REPO}/issues", json=_issue(7), status=201)

    result = await lachesis.call_tool(
        "create_github_issues",
        {
            "repo": REPO,
            "issues": [{"title": "Keep widgets", "body": "Build it.", "capability": "backlog"}],
        },
    )

    assert result.is_error
    assert "spec_change" in result.text
    assert "spec_gap" in result.text
    assert _created(lachesis) == []


@scenario("issues", "Implementing a spec change")
async def test_implementing_a_spec_change(lachesis: Lachesis) -> None:
    _uses_specs(lachesis)
    await _register(lachesis)
    spec_change = {
        **_issue(152, labels=("spec-change",)),
        "pull_request": {"html_url": f"https://github.com/{REPO}/pull/152"},
    }
    _holds(lachesis, spec_change)
    lachesis.github.on("POST", f"/repos/{REPO}/issues", json=_issue(153), status=201)

    await _create(
        lachesis,
        {
            "title": "Say what to work on next",
            "body": "Build it.",
            "spec_change": 152,
            "capability": "backlog",
            "requirement": "The system says what to work on next",
        },
    )

    [created] = _created(lachesis)
    assert created["body"].endswith(
        "\n---\n"
        "Spec change: #152\n"
        "Capability: backlog\n"
        "Requirement: The system says what to work on next"
    )


@scenario("issues", "Audit finding")
async def test_audit_finding(lachesis: Lachesis) -> None:
    _uses_specs(lachesis)
    await _register(lachesis)
    lachesis.github.on("POST", f"/repos/{REPO}/labels", json={"name": "spec-gap"}, status=201)
    lachesis.github.on("POST", f"/repos/{REPO}/issues", json=_issue(7), status=201)

    await _create(
        lachesis,
        {
            "title": "The backlog skips parked issues",
            "body": "The audit found parked issues offered next.",
            "spec_gap": True,
            "capability": "backlog",
            "requirement": "The system says what to work on next",
        },
    )

    [created] = _created(lachesis)
    assert "spec-gap" in created["labels"]
    assert created["body"].endswith(
        "\n---\n"
        "Spec gap: yes\n"
        "Capability: backlog\n"
        "Requirement: The system says what to work on next"
    )


@scenario("issues", "Closing an issue")
async def test_closing_an_issue(lachesis: Lachesis) -> None:
    github = lachesis.github
    await _register(lachesis)
    _holds(lachesis, _issue(7))
    github.on("PATCH", f"/repos/{REPO}/issues/7", json=_issue(7, state="closed"))

    closed = await lachesis.call_tool(
        "update_github_issue",
        {"repo": REPO, "issue_number": 7, "fields": {"state": "closed"}},
    )
    assert not closed.is_error, closed.text
    [patch] = github.requests_to("PATCH", f"/repos/{REPO}/issues/7")
    _holds(lachesis, {**_issue(7), **patch.body})
    read = await lachesis.call_tool("get_github_issue", {"repo": REPO, "issue_number": 7})

    assert closed.json()["number"] == 7
    assert not read.is_error, read.text
    assert read.json()["state"] == "closed"


@scenario("issues", "Closed without merging")
async def test_closed_without_merging(lachesis: Lachesis) -> None:
    await _register(lachesis)
    lachesis.github.on(
        "GET", f"/repos/{REPO}/pulls/12", json=_pull(12, state="closed", merged=False)
    )

    result = await lachesis.call_tool("get_pull_request", {"repo": REPO, "pr_number": 12})

    assert not result.is_error, result.text
    pull = result.json()
    assert (pull["state"], pull["merged"]) == ("closed", False)


@scenario("issues", "Unknown number")
async def test_unknown_number(lachesis: Lachesis) -> None:
    await _register(lachesis)

    result = await lachesis.call_tool("get_pull_request", {"repo": REPO, "pr_number": 9999})

    assert not result.is_error, result.text
    assert result.json()["found"] is False


@scenario("issues", "Reading a worker's question")
async def test_reading_a_workers_question(lachesis: Lachesis) -> None:
    await _register(lachesis)
    asked_at = (NOW - timedelta(hours=2)).isoformat()
    question = "Should a parked issue keep its position, or go last when unparked?"
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/issues/160/comments",
        json=[
            _comment(1, "author", (NOW - timedelta(days=1)).isoformat(), "Please build this."),
            _comment(2, "factory-worker", asked_at, question),
        ],
    )

    result = await lachesis.call_tool("get_comments", {"repo": REPO, "number": 160})

    assert not result.is_error, result.text
    latest = result.json()[-1]
    assert (latest["body"], latest["author"], latest["created_at"]) == (
        question,
        "factory-worker",
        asked_at,
    )


@scenario("issues", "Asking")
async def test_asking(lachesis: Lachesis) -> None:
    await _register(lachesis)
    _holds(lachesis, _issue(160))
    _waits_for_answers(lachesis, 160)
    question = "Should a parked issue keep its position, or go last when unparked?"

    asked = await lachesis.call_tool(
        "ask_author", {"repo": REPO, "number": 160, "question": question}
    )
    waiting = await lachesis.call_tool("list_open_questions", {})

    assert not asked.is_error, asked.text
    assert question in _comments_posted(lachesis, 160)[-1]
    labelled = lachesis.github.requests_to("POST", f"/repos/{REPO}/issues/160/labels")
    assert [request.body["labels"] for request in labelled] == [["needs-author"]]
    assert not waiting.is_error, waiting.text
    [open_question] = waiting.json()
    assert open_question["blocks"] == [160]


@scenario("issues", "Answering")
async def test_answering(lachesis: Lachesis) -> None:
    await _register(lachesis)
    _holds(lachesis, _issue(160))
    _waits_for_answers(lachesis, 160)
    asked = await lachesis.call_tool(
        "ask_author", {"repo": REPO, "number": 160, "question": "Keep its position?"}
    )
    assert not asked.is_error, asked.text
    _holds(lachesis, _issue(160, labels=("needs-author",)))

    answered = await lachesis.call_tool(
        "answer_author", {"repo": REPO, "number": 160, "answer": "Keep it."}
    )

    assert not answered.is_error, answered.text
    assert _comments_posted(lachesis, 160)[-1] == "Keep it."
    assert lachesis.github.requests_to("DELETE", f"/repos/{REPO}/issues/160/labels/needs-author")
