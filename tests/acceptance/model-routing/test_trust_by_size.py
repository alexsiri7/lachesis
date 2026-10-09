"""Scorecard and trust per model, kind and size; trials budgeted (spec change #303).

The contract these tests set for the implementation issues:

- ``size_issue(repo, issue, size)`` sizes an issue; runs are scored at their issue's size.
- ``read_scorecard`` entries per model and kind carry ``sizes``: one entry per size with
  runs, merged_without_rework, retries, audit_findings, tokens, cost_usd,
  ``tokens_per_point``, ``cost_per_point`` and ``trusted``.
- ``route_run(kind, size=None, repo=None, issue=None)`` returns ``model`` and, for a run by
  a model not yet trusted for that kind and size, ``trial: true``.
- ``read_sprint`` adds ``trial_budget_tokens``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from harness import Lachesis, scenario

REPO = "owner/repo"
OPUS = "claude"
SONNET = "claude-sonnet"
HAIKU = "claude-haiku"
NOW = datetime.now(UTC)


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


def _issue(number: int, *, closed: bool) -> dict[str, Any]:
    stamp = (NOW - timedelta(hours=2)).isoformat()
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": "closed" if closed else "open",
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": stamp,
        "updated_at": stamp,
        "closed_at": stamp if closed else None,
        "state_reason": "completed" if closed else None,
    }


class Factory:
    """One repository, its issues and the runs reported on them."""

    def __init__(self, lachesis: Lachesis) -> None:
        self.lachesis = lachesis
        self.items: dict[int, dict[str, Any]] = {}
        self.next_number = 100

    async def start(self) -> Factory:
        await _ok(self.lachesis, "register_repo", {"repo": REPO})
        await _ok(
            self.lachesis,
            "register_account",
            {"name": "factory", "share_percent": 100, "primary": True},
        )
        await _ok(
            self.lachesis,
            "report_fuel",
            {
                "account": "factory",
                "weekly_used_percent": 10,
                "weekly_resets_at": (NOW + timedelta(days=3)).isoformat(),
                "overflow_spent_usd": 0,
                "overflow_cap_usd": 0,
            },
        )
        self.lachesis.github.on("GET", f"/repos/{REPO}/pulls", json=[])
        self._publish()
        return self

    def _publish(self) -> None:
        self.lachesis.github.on("GET", f"/repos/{REPO}/issues", json=list(self.items.values()))
        for number, item in self.items.items():
            self.lachesis.github.on("GET", f"/repos/{REPO}/issues/{number}", json=item)

    async def runs(
        self,
        model: str,
        kind: str,
        size: int,
        count: int,
        *,
        tokens: int = 100,
        rework: int = 0,
        closed: bool = True,
    ) -> list[int]:
        """*count* runs of *kind* by *model* on fresh issues of *size*; the last *rework* of
        them needed rework."""
        numbers = []
        for index in range(count):
            number = self.next_number
            self.next_number += 1
            numbers.append(number)
            self.items[number] = _issue(number, closed=closed)
            self._publish()
            await _ok(self.lachesis, "size_issue", {"repo": REPO, "issue": number, "size": size})
            await _ok(
                self.lachesis,
                "report_usage",
                {
                    "repo": REPO,
                    "issue": number,
                    "model": model,
                    "kind": kind,
                    "tokens_in": tokens // 2,
                    "tokens_out": tokens - tokens // 2,
                    "account": "factory",
                },
            )
            if index >= count - rework:
                await _ok(
                    self.lachesis,
                    "report_run_outcome",
                    {"repo": REPO, "issue": number, "kind": kind, "outcome": "rework"},
                )
        return numbers

    async def route(self, kind: str, size: int, **extra: Any) -> dict[str, Any]:
        return await _ok(self.lachesis, "route_run", {"kind": kind, "size": size, **extra})

    async def size_entry(self, model: str, kind: str, size: int) -> dict[str, Any]:
        card = await _ok(self.lachesis, "read_scorecard", {})
        [entry] = [m for m in card["models"] if m["model"] == model]
        [by_kind] = [k for k in entry["kinds"] if k["kind"] == kind]
        [by_size] = [s for s in by_kind["sizes"] if s["size"] == size]
        return by_size


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("model-routing", "Reading the scorecard")
async def test_reading_the_scorecard(lachesis: Lachesis) -> None:
    factory = await Factory(lachesis).start()
    await factory.runs(OPUS, "triage", 1, 2, tokens=10)
    await factory.runs(OPUS, "implementation", 3, 2, tokens=90)
    await factory.runs(SONNET, "triage", 1, 2, tokens=6)
    await factory.runs(SONNET, "implementation", 3, 2, tokens=60)

    for model, kind, size, per_point in (
        (OPUS, "triage", 1, 10),
        (OPUS, "implementation", 3, 30),
        (SONNET, "triage", 1, 6),
        (SONNET, "implementation", 3, 20),
    ):
        entry = await factory.size_entry(model, kind, size)
        assert entry["runs"] == 2
        assert entry["merged_without_rework"] == 2
        assert entry["tokens_per_point"] == per_point
        for key in ("retries", "audit_findings", "tokens", "cost_usd", "cost_per_point"):
            assert key in entry


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("model-routing", "Cheap model for triage")
async def test_cheap_model_for_triage(lachesis: Lachesis) -> None:
    factory = await Factory(lachesis).start()
    await factory.runs(HAIKU, "triage", 1, 5, tokens=10)
    await factory.runs(OPUS, "triage", 1, 2, tokens=100)

    routed = await factory.route("triage", 1)

    assert routed["model"] == HAIKU


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("model-routing", "Smaller model for small work")
async def test_smaller_model_for_small_work(lachesis: Lachesis) -> None:
    factory = await Factory(lachesis).start()
    await factory.runs(SONNET, "tests", 3, 5, tokens=30)  # earns the right to trial upward
    await factory.runs(SONNET, "implementation", 3, 5, tokens=30)  # 10 per point
    await factory.runs(OPUS, "implementation", 3, 2, tokens=90)  # 30 per point

    small = await factory.route("implementation", 2)
    large = await factory.route("implementation", 5)

    assert small["model"] == SONNET
    assert not small.get("trial")
    assert large["model"] != SONNET or large.get("trial") is True


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("model-routing", "Earning trust")
async def test_earning_trust(lachesis: Lachesis) -> None:
    factory = await Factory(lachesis).start()
    await factory.runs(HAIKU, "triage", 1, 4)
    assert (await factory.size_entry(HAIKU, "triage", 1))["trusted"] is False

    await factory.runs(HAIKU, "triage", 1, 1)

    assert (await factory.size_entry(HAIKU, "triage", 1))["trusted"] is True


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("model-routing", "Stepping up a size")
async def test_stepping_up_a_size(lachesis: Lachesis) -> None:
    factory = await Factory(lachesis).start()
    await factory.runs(SONNET, "tests", 2, 5)
    await factory.runs(SONNET, "implementation", 2, 5, tokens=10)
    await factory.runs(OPUS, "implementation", 2, 2, tokens=100)

    three = await factory.route("implementation", 3)
    five = await factory.route("implementation", 5)

    assert three["model"] == SONNET
    assert three["trial"] is True
    assert five["model"] != SONNET


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("model-routing", "Losing trust")
async def test_losing_trust(lachesis: Lachesis) -> None:
    factory = await Factory(lachesis).start()
    await factory.runs(SONNET, "tests", 2, 5)
    assert (await factory.size_entry(SONNET, "tests", 2))["trusted"] is True

    await factory.runs(SONNET, "tests", 2, 2, rework=2)

    assert (await factory.size_entry(SONNET, "tests", 2))["trusted"] is False


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("model-routing", "Trial share respected")
async def test_trial_share_respected(lachesis: Lachesis) -> None:
    await Factory(lachesis).start()

    sprint = await _ok(lachesis, "read_sprint", {})

    assert sprint["trial_budget_tokens"] == round(sprint["budget_tokens"] * 0.10)
    trials = [entry["estimate_tokens"] or 0 for entry in sprint["plan"] if entry.get("trial")]
    assert sum(trials) <= sprint["trial_budget_tokens"]


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("model-routing", "Failed trial redone")
async def test_failed_trial_redone(lachesis: Lachesis) -> None:
    factory = await Factory(lachesis).start()
    await factory.runs(HAIKU, "tests", 1, 5)  # trusted for tests, so it may trial implementation
    trial = await factory.route("implementation", 1)
    assert trial["model"] == HAIKU and trial["trial"] is True
    [failed] = await factory.runs(HAIKU, "implementation", 1, 1, rework=1, closed=False)

    redo = await factory.route("implementation", 1, repo=REPO, issue=failed)

    assert redo["model"] == OPUS
    assert not redo.get("trial")
