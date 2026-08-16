"""Unit tests for mcp/web.py — _extract_section helper."""

from __future__ import annotations

import sys
import os

# Ensure mcp/ is on the path so we can import web directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web import _extract_section


def test_extracts_section_in_middle():
    body = "## Why\nBecause users need it.\n## What\nA button."
    assert _extract_section(body, "Why") == "Because users need it."


def test_extracts_last_section():
    body = "## Why\nFirst reason.\n## What\nThe deliverable."
    assert _extract_section(body, "What") == "The deliverable."


def test_returns_empty_string_when_section_missing():
    body = "## Why\nSome reason."
    assert _extract_section(body, "What") == ""


def test_returns_empty_string_for_empty_body():
    assert _extract_section("", "Why") == ""


def test_returns_empty_string_for_none_body():
    """post.content can be None when a .md file has no body."""
    assert _extract_section(None, "Why") == ""  # type: ignore[arg-type]


def test_strips_trailing_whitespace():
    body = "## Why\n  Leading and trailing.  \n## What\nX."
    assert _extract_section(body, "Why") == "Leading and trailing."


def test_multiline_section_content():
    body = "## Why\nLine one.\nLine two.\n## What\nX."
    result = _extract_section(body, "Why")
    assert "Line one." in result
    assert "Line two." in result


def test_section_at_start_of_body():
    """No preceding content before first ## — ^ anchor must fire."""
    body = "## Why\nReason here."
    assert _extract_section(body, "Why") == "Reason here."


def test_special_regex_chars_in_heading():
    """re.escape must handle headings containing special regex characters."""
    body = "## Why (v2)\nReason."
    assert _extract_section(body, "Why (v2)") == "Reason."


def test_crlf_line_endings_are_normalized():
    """GitHub files pushed from Windows may have CRLF line endings."""
    body = "## Why\r\nReason with CRLF.\r\n## What\r\nDeliverable."
    assert _extract_section(body, "Why") == "Reason with CRLF."


def test_crlf_last_section():
    body = "## Why\r\nFirst reason.\r\n## What\r\nThe deliverable."
    assert _extract_section(body, "What") == "The deliverable."
