"""The secrets rules, given in every workflow the guidance returns (spec change #354).

"Any repository" is each of the three answers ``get_initial_instructions`` gives: for a
repository that uses specs, for one that does not, and with no repository named. The worker's
workflow is the unattended agent's, which ``get_morning_briefing`` returns.
"""

from __future__ import annotations

import pytest

from harness import Lachesis, scenario

SPEC_REPO = "owner/specs"
PLAIN_REPO = "owner/plain"


async def _every_workflow(lachesis: Lachesis) -> list[str]:
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
    guidance = []
    for tool, arguments in calls:
        result = await lachesis.call_tool(tool, arguments)
        assert not result.is_error, result.text
        text = result.json()
        assert isinstance(text, str)
        guidance.append(" ".join(text.lower().split()))
    return guidance


@pytest.mark.xfail(strict=True, reason="spec change #354")
@scenario("agent-guidance", "A new service needs a session secret")
async def test_a_new_service_needs_a_session_secret(lachesis: Lachesis) -> None:
    for guidance in await _every_workflow(lachesis):
        assert "doppler" in guidance
        assert "generate" in guidance
        assert "pipe" in guidance
        assert "never print" in guidance


@pytest.mark.xfail(strict=True, reason="spec change #354")
@scenario("agent-guidance", "A secret value is never exposed")
async def test_a_secret_value_is_never_exposed(lachesis: Lachesis) -> None:
    for guidance in await _every_workflow(lachesis):
        assert "never print" in guidance
        for place in ("repository", "issue", "pull request", "comment", "work note", "log"):
            assert place in guidance, place


@pytest.mark.xfail(strict=True, reason="spec change #354")
@scenario("agent-guidance", "A key only the author can get")
async def test_a_key_only_the_author_can_get(lachesis: Lachesis) -> None:
    for guidance in await _every_workflow(lachesis):
        assert "author task" in guidance or "author_task" in guidance
        assert "doppler" in guidance
        assert "project" in guidance
        assert "variable" in guidance
