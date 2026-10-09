"""The morning briefing closes the week and lists the defaults the factory took.

The briefing is instructions for an agent, so these tests read what get_morning_briefing
serves and check that each scenario's instruction is in it. The contract these tests set for
the implementation issues of spec change #319: the briefing names ``report_sprint`` for the
first morning after the primary account's weekly reset, and tells the agent to list each
question settled by default (outcome ``defaulted``) since the last briefing, one line each.
"""

from __future__ import annotations

import pytest

from harness import Lachesis, scenario


async def _briefing(lachesis: Lachesis) -> str:
    result = await lachesis.call_tool("get_morning_briefing", {})
    assert not result.is_error, result.text
    text = result.json()
    assert isinstance(text, str)
    return " ".join(text.lower().split())


@scenario("agent-guidance", "Sprint just ended")
async def test_sprint_just_ended(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "report_sprint" in briefing
    assert "first morning after the weekly reset" in briefing
    assert "say in one line that the sprint review was sent" in briefing


@scenario("agent-guidance", "Defaults taken")
async def test_defaults_taken(lachesis: Lachesis) -> None:
    briefing = await _briefing(lachesis)

    assert "defaulted" in briefing
    assert "since the last briefing" in briefing
    assert "one line each" in briefing
