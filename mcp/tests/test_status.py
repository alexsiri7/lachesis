"""Unit tests for mcp/status.py — the shared requirement status vocabulary."""

from __future__ import annotations

from status import INACTIVE_STATUSES, VALID_STATUSES, canonical_status


def test_canonical_status_folds_the_variant_spelling() -> None:
    """'in_progress' and 'in-progress' are the same state."""
    assert canonical_status("in_progress") == "in-progress"
    assert canonical_status("in-progress") == "in-progress"


def test_canonical_status_folds_case_and_whitespace() -> None:
    """Surrounding whitespace and capitalisation do not make a new state."""
    assert canonical_status(" In_Progress ") == "in-progress"
    assert canonical_status("DONE") == "done"


def test_canonical_status_of_missing_value_is_empty() -> None:
    """A requirement with no status reads as the empty string, not a crash."""
    assert canonical_status(None) == ""
    assert canonical_status("") == ""


def test_canonical_status_does_not_invent_a_mapping() -> None:
    """An unrecognised value is normalised but never mapped onto a known state."""
    assert canonical_status("blocked") == "blocked"
    assert canonical_status("Not_A_Status") == "not-a-status"


def test_new_lifecycle_states_are_valid_and_inactive() -> None:
    """cancelled and superseded are real states, excluded from active listings."""
    assert {"cancelled", "superseded"} <= VALID_STATUSES
    assert INACTIVE_STATUSES == {"cancelled", "superseded"}
    assert "done" in VALID_STATUSES
    assert "done" not in INACTIVE_STATUSES
