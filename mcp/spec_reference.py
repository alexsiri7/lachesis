"""The block at the end of an issue body naming the spec work the issue implements."""

from __future__ import annotations

import re
from typing import NamedTuple

_SEPARATOR = "---"
_SPEC_CHANGE = re.compile(r"#(\d+)")


class SpecReference(NamedTuple):
    """A spec change (pull request number) or, when ``spec_change`` is None, a spec gap."""

    spec_change: int | None
    capability: str
    requirement: str | None = None


def format_reference(ref: SpecReference) -> str:
    """Render *ref* as the block the issues spec prescribes, without a trailing newline."""
    lines = [_SEPARATOR]
    if ref.spec_change is None:
        lines.append("Spec gap: yes")
    else:
        lines.append(f"Spec change: #{ref.spec_change}")
    lines.append(f"Capability: {ref.capability}")
    if ref.requirement is not None:
        lines.append(f"Requirement: {ref.requirement}")
    return "\n".join(lines)


def append_reference(body: str, ref: SpecReference) -> str:
    """Return *body* ending with *ref*'s block.

    The blank line matters: a ``---`` directly under text is a Markdown heading underline.
    """
    stripped = body.rstrip()
    block = format_reference(ref)
    return f"{stripped}\n\n{block}" if stripped else block


def parse_reference(body: str) -> SpecReference | None:
    """Read the reference block from the end of an issue *body*, or None if it has none.

    Only the lines after the last ``---`` are read, so a horizontal rule earlier in the
    body is not mistaken for the block.
    """
    lines = [line.rstrip() for line in body.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    if _SEPARATOR not in lines:
        return None
    start = len(lines) - lines[::-1].index(_SEPARATOR)

    fields: dict[str, str] = {}
    for line in lines[start:]:
        key, sep, value = line.partition(":")
        if not sep or key in fields:
            return None
        fields[key] = value.strip()

    capability = fields.pop("Capability", "")
    requirement = fields.pop("Requirement", None)
    spec_change = fields.pop("Spec change", None)
    spec_gap = fields.pop("Spec gap", None)
    if fields or not capability or (spec_change is None) == (spec_gap is None):
        return None
    if spec_gap is not None:
        return SpecReference(None, capability, requirement) if spec_gap == "yes" else None
    match = _SPEC_CHANGE.fullmatch(spec_change or "")
    return SpecReference(int(match.group(1)), capability, requirement) if match else None
