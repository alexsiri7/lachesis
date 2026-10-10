"""The secrets rules, given in every workflow the guidance returns (spec change #354).

"Any repository" is each of the three answers ``get_initial_instructions`` gives: for a
repository that uses specs, for one that does not, and with no repository named.
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

    guidance = []
    for arguments in ({"repo": SPEC_REPO}, {"repo": PLAIN_REPO}, {}):
        result = await lachesis.call_tool("get_initial_instructions", arguments)
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
@scenario("agent-guidance", "A key only the author can get")
async def test_a_key_only_the_author_can_get(lachesis: Lachesis) -> None:
    for guidance in await _every_workflow(lachesis):
        assert "author task" in guidance or "author_task" in guidance
        assert "doppler" in guidance
        assert "project" in guidance
        assert "variable" in guidance
