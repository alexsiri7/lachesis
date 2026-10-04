"""Tests for mcp/tools/fuel.py — run usage reports."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from auth import current_db_user_id
from tools.fuel import report_usage

_REGISTERED = [{"id": "r1", "repo_name": "alexsiri7/lachesis", "created_at": "t"}]


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


@pytest.fixture()
def registered():
    with patch("tools.requirements.db.list_repos", return_value=_REGISTERED):
        yield


async def test_overflow_run_is_stored_with_model_tokens_cost_issue_and_kind(
    set_user_id: str, registered: None
) -> None:
    """Scenario: Run on overflow model — stored with model, tokens, cost, issue 160 and kind."""
    stored = {"id": 3, "issue": 160, "model": "deepseek/deepseek-chat", "cost_usd": 0.42}
    with patch("tools.fuel.db.record_usage_report", return_value=stored) as record:
        result = await report_usage(
            "lachesis", 160, " deepseek/deepseek-chat ", "tests", 120_000, 8_000, cost_usd=0.42
        )

    assert result == stored
    record.assert_called_once_with(
        "test-uid",
        "alexsiri7/lachesis",
        160,
        "deepseek/deepseek-chat",
        "tests",
        120_000,
        8_000,
        0.42,
    )


async def test_a_run_on_the_claude_allowance_has_no_cost(
    set_user_id: str, registered: None
) -> None:
    with patch("tools.fuel.db.record_usage_report", return_value={"id": 4}) as record:
        await report_usage("lachesis", 160, "claude-opus-5-5", "implementation", 900, 300)

    assert record.call_args[0][-1] is None


@pytest.mark.parametrize(
    ("issue", "model", "kind", "tokens_in", "tokens_out", "cost", "match"),
    [
        (0, "m", "tests", 1, 1, None, "must name an issue"),
        (160, "  ", "tests", 1, 1, None, "model"),
        (160, "m", "coding", 1, 1, None, "kind must be one of"),
        (160, "m", "tests", -1, 1, None, "tokens"),
        (160, "m", "tests", 1, -1, None, "tokens"),
        (160, "m", "tests", 1, 1, -0.01, "cost_usd"),
    ],
)
async def test_refuses_an_incomplete_report(
    set_user_id: str,
    registered: None,
    issue: int,
    model: str,
    kind: str,
    tokens_in: int,
    tokens_out: int,
    cost: float | None,
    match: str,
) -> None:
    with (
        patch("tools.fuel.db.record_usage_report") as record,
        pytest.raises(ValueError, match=match),
    ):
        await report_usage("lachesis", issue, model, kind, tokens_in, tokens_out, cost)  # type: ignore[arg-type]

    record.assert_not_called()


async def test_refuses_an_unregistered_repository(set_user_id: str, registered: None) -> None:
    with (
        patch("tools.fuel.db.record_usage_report") as record,
        pytest.raises(ValueError, match="not registered"),
    ):
        await report_usage("owner/other", 1, "m", "triage", 1, 1)

    record.assert_not_called()
