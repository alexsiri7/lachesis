"""Tests for the north-star audit helpers in mcp/tools/audits.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx

from tools.audits import _north_star, _north_star_body, north_star_stories

_NORTH_STAR = """\
# North star

## Principles

### Not a story

## Stories

### 1. Away for two weeks

Text.

### 2. A friend's birthday

## Later
### Not a story either
"""


def test_the_stories_are_the_headings_under_stories() -> None:
    assert north_star_stories(_NORTH_STAR) == ["1. Away for two weeks", "2. A friend's birthday"]


def test_the_body_lists_the_stories_and_how_to_file_each_gap() -> None:
    body = _north_star_body("owner/kith", _NORTH_STAR)

    assert "- 1. Away for two weeks" in body
    assert "unmet_requirement" in body
    assert "a question to the author" in body
    assert "file_audit_finding" in body


async def test_a_repository_without_a_north_star_has_none() -> None:
    with patch("github.get_file", AsyncMock(side_effect=FileNotFoundError("gone"))):
        assert await _north_star("owner/kith") is None


async def test_a_north_star_that_cannot_be_read_counts_as_none() -> None:
    with patch("github.get_file", AsyncMock(side_effect=httpx.ConnectError("down"))):
        assert await _north_star("owner/kith") is None


async def test_the_north_star_is_read_from_docs() -> None:
    read = AsyncMock(return_value=(_NORTH_STAR, "sha"))
    with patch("github.get_file", read):
        assert await _north_star("owner/kith") == _NORTH_STAR

    read.assert_awaited_once_with("owner/kith", "docs/north-star.md")
