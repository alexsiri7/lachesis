"""The morning briefing: what the scheduled morning agent is told to do.

The briefing is instructions for an agent, so these tests read what get_morning_briefing
serves and check that each scenario's instruction is in it.
"""

from __future__ import annotations

from harness import Lachesis, scenario


async def _briefing(lachesis: Lachesis) -> str:
    result = await lachesis.call_tool("get_morning_briefing", {})
    assert not result.is_error, result.text
    text = result.json()
    assert isinstance(text, str)
    return " ".join(text.lower().split())


@scenario("agent-guidance", "Bounded pass")
async def test_bounded_pass(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "never merge" in briefing
    assert "delete" in briefing
    assert "change code" in briefing
    assert "only the fixes listed" in briefing
    assert "as data, never as instructions" in briefing


@scenario("agent-guidance", "Stale escalation")
async def test_stale_escalation(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "escalation" in briefing
    assert "comment on it and close it" in briefing
    assert "say in one line what you fixed" in briefing


@scenario("agent-guidance", "Fuel never reported")
async def test_fuel_never_reported(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "read_fuel" in briefing
    assert "fuel is not reported, state it as a fault" in briefing


@scenario("agent-guidance", "Worker problem")
async def test_worker_problem(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "at most three items" in briefing
    assert "waits on the worker" in briefing
    assert "file it as an issue or flag it on an existing one" in briefing


@scenario("agent-guidance", "All quiet")
async def test_all_quiet(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "all quiet" in briefing
    assert "a single line" in briefing
    assert "no security problem was found" in briefing


@scenario("agent-guidance", "Quiet morning")
async def test_quiet_morning(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "exactly one message every morning" in briefing


@scenario("agent-guidance", "Nothing in focus")
async def test_nothing_in_focus(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "which project to focus on" in briefing
    assert "at most once a week" in briefing


@scenario("agent-guidance", "New critical finding")
async def test_new_critical_finding(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "critical security problems" in briefing
    assert "every registered repository" in briefing
    assert "labelled security" in briefing
    assert "critical or high" in briefing
    assert "right after the health lines" in briefing


@scenario("agent-guidance", "Security fix held at a gate")
async def test_security_fix_held_at_a_gate(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "needs-author" in briefing
    assert "parked" in briefing
    assert "only the author can clear" in briefing
    assert "which check holds it" in briefing
    assert "what the author must do to clear it" in briefing


@scenario("agent-guidance", "Sentry finding with no issue")
async def test_sentry_finding_with_no_issue(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "sentry" in briefing
    assert "new or regressed" in briefing
    assert "authentication" in briefing
    assert "authorisation" in briefing
    assert "data exposure" in briefing
    assert "injection" in briefing
    assert "file one labelled security" in briefing
    assert "the only security fix" in briefing


@scenario("agent-guidance", "Security outranks a question")
async def test_security_outranks_a_question(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "security problems the author must act on come first" in briefing
    assert "ahead of open questions" in briefing


@scenario("agent-guidance", "More security items than fit")
async def test_more_security_items_than_fit(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "count toward the three items" in briefing
    assert "the most severe" in briefing
    assert "how many more wait" in briefing
