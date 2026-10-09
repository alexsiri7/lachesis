"""Guidance for implementers in a repository that uses specs."""

from __future__ import annotations

import pytest

from harness import Lachesis, scenario

REPO = "owner/repo"


@pytest.mark.xfail(strict=True, reason="spec change #173")
@scenario("agent-guidance", "Implementer asks about tests")
async def test_implementer_asks_about_tests(lachesis: Lachesis) -> None:
    await lachesis.call_tool("register_repo", {"repo": REPO})
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "widgets", "path": "openspec/specs/widgets"}],
    )

    result = await lachesis.call_tool("get_initial_instructions", {"repo": REPO})

    assert not result.is_error, result.text
    text = result.json()
    assert isinstance(text, str)
    guidance = " ".join(text.lower().split())
    assert "acceptance test" in guidance
    assert "only remove expected-failure markers" in guidance
