"""The founding rule: the author is never on the critical path.

"Any instructions requested" is each instruction set the system returns: the workflow for a
repository that uses specs, for one that does not, with no repository named, and the morning
briefing.
"""

from __future__ import annotations

import pytest

from harness import Lachesis, scenario

SPEC_REPO = "owner/specs"
PLAIN_REPO = "owner/plain"
FOUNDING_RULE = "the author is never on the critical path"


async def _every_instruction_set(lachesis: Lachesis) -> list[str]:
    for repo in (SPEC_REPO, PLAIN_REPO):
        await lachesis.call_tool("register_repo", {"repo": repo})
    lachesis.github.on(
        "GET",
        f"/repos/{SPEC_REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "widgets", "path": "openspec/specs/widgets"}],
    )
    lachesis.github.on("GET", f"/repos/{PLAIN_REPO}/contents/openspec/specs", json=[])

    calls = [
        ("get_initial_instructions", {"repo": SPEC_REPO}),
        ("get_initial_instructions", {"repo": PLAIN_REPO}),
        ("get_initial_instructions", {}),
        ("get_morning_briefing", {}),
    ]
    instructions = []
    for tool, arguments in calls:
        result = await lachesis.call_tool(tool, arguments)
        assert not result.is_error, result.text
        text = result.json()
        assert isinstance(text, str)
        instructions.append(text)
    return instructions


@pytest.mark.xfail(strict=True, reason="spec gap")
@scenario("agent-guidance", "Any instructions requested")
async def test_any_instructions_requested(lachesis: Lachesis) -> None:
    for text in await _every_instruction_set(lachesis):
        opening = " ".join(text.split("\n\n", 1)[0].lower().split())
        assert FOUNDING_RULE in opening, text[:200]


@pytest.mark.xfail(strict=True, reason="spec gap")
@scenario("agent-guidance", "Work that needs a decision")
async def test_work_that_needs_a_decision(lachesis: Lachesis) -> None:
    workflows = (await _every_instruction_set(lachesis))[:3]
    for text in workflows:
        guidance = " ".join(text.lower().split())
        assert "decision only the author can make" in guidance
        assert "ask_question" in guidance
        assert "move on" in guidance
        assert "never park" in guidance
        assert "waits for the author" in guidance
