"""The workflow guidance: which workflow applies, the question loop, and the morning pointer.

"Behaviour changes" is a rule about how changes land; through the public interface it is
checked as the guidance describing how issues become eligible for pickup as next_issue
actually offers them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from harness import Lachesis, scenario

SPEC_REPO = "owner/specs"
PLAIN_REPO = "owner/plain"
NOW = datetime.now(UTC)


async def _register(lachesis: Lachesis) -> None:
    for repo in (SPEC_REPO, PLAIN_REPO):
        await lachesis.call_tool("register_repo", {"repo": repo})
    lachesis.github.on(
        "GET",
        f"/repos/{SPEC_REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "widgets", "path": "openspec/specs/widgets"}],
    )
    lachesis.github.on("GET", f"/repos/{PLAIN_REPO}/contents/openspec/specs", json=[])


async def _guidance(lachesis: Lachesis, repo: str | None = None) -> str:
    arguments = {} if repo is None else {"repo": repo}
    result = await lachesis.call_tool("get_initial_instructions", arguments)
    assert not result.is_error, result.text
    text = result.json()
    assert isinstance(text, str)
    return " ".join(text.lower().split())


def _issue(number: int, age: timedelta) -> dict[str, Any]:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": "open",
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/{PLAIN_REPO}/issues/{number}",
        "created_at": (NOW - age).isoformat(),
        "updated_at": (NOW - age).isoformat(),
        "closed_at": None,
        "state_reason": None,
    }


async def _next(lachesis: Lachesis) -> dict[str, Any]:
    result = await lachesis.call_tool("next_issue", {"repo": PLAIN_REPO})
    assert not result.is_error, result.text
    return result.json()


@scenario("agent-guidance", "Scheduled task")
async def test_scheduled_task(lachesis: Lachesis) -> None:
    await _register(lachesis)

    for repo in (SPEC_REPO, PLAIN_REPO, None):
        guidance = await _guidance(lachesis, repo)
        assert "call get_morning_briefing and follow the instructions it returns" in guidance


@scenario("agent-guidance", "Session start without a repository")
async def test_session_start_without_a_repository(lachesis: Lachesis) -> None:
    guidance = await _guidance(lachesis)

    assert "spec-change workflow" in guidance
    assert "propose_spec_change" in guidance
    assert "requirement-file workflow" in guidance
    assert "create_requirement" in guidance
    assert "link_issues" in guidance
    assert "a repository uses specs when its default branch contains openspec/specs/" in guidance


@scenario("agent-guidance", "Session start in a spec repository")
async def test_session_start_in_a_spec_repository(lachesis: Lachesis) -> None:
    await _register(lachesis)

    guidance = await _guidance(lachesis, SPEC_REPO)

    assert "spec-change workflow" in guidance
    assert "propose_spec_change" in guidance
    assert "requirement-file workflow" not in guidance
    assert "link_issues" not in guidance


@scenario("agent-guidance", "Stuck on intent")
async def test_stuck_on_intent(lachesis: Lachesis) -> None:
    await _register(lachesis)

    for repo in (SPEC_REPO, PLAIN_REPO, None):
        guidance = await _guidance(lachesis, repo)
        assert "do not guess" in guidance
        assert "ask_author" in guidance
        assert "move on to other work" in guidance


@scenario("agent-guidance", "Behaviour changes")
async def test_behaviour_changes(lachesis: Lachesis) -> None:
    await _register(lachesis)
    created_now = _issue(1, timedelta(minutes=1))
    parked = _issue(2, timedelta(days=2))
    lachesis.github.on("GET", f"/repos/{PLAIN_REPO}/issues", json=[created_now, parked])
    for item in (created_now, parked):
        lachesis.github.on("GET", f"/repos/{PLAIN_REPO}/issues/{item['number']}", json=item)
    flagged = await lachesis.call_tool(
        "set_backlog_flags", {"repo": PLAIN_REPO, "issue_number": 2, "ready": False}
    )
    assert not flagged.is_error, flagged.text

    offered = await _next(lachesis)
    guidance = await _guidance(lachesis, PLAIN_REPO)

    assert offered["issue"]["number"] == 1
    assert "eligible for autonomous pickup as soon as they are created" in guidance
    assert "set_backlog_flags with ready=false to park" in guidance
