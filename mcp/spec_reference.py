"""The block at the end of an issue body naming the spec work the issue implements."""

from __future__ import annotations

import re
from typing import NamedTuple

_SEPARATOR = "---"
_SPEC_CHANGE = re.compile(r"#(\d+)")


class SpecReference(NamedTuple):
    """A spec change (pull request number) or, when ``spec_change`` is None, a spec gap.

    Only a spec change's acceptance-tests issue names more than one capability.
    """

    spec_change: int | None
    capabilities: tuple[str, ...]
    requirement: str | None = None

    @property
    def capability(self) -> str | None:
        """The one capability named, or None when several are."""
        return self.capabilities[0] if len(self.capabilities) == 1 else None


def format_reference(ref: SpecReference) -> str:
    """Render *ref* as the block the issues spec prescribes, without a trailing newline."""
    lines = [_SEPARATOR]
    if ref.spec_change is None:
        lines.append("Spec gap: yes")
    else:
        lines.append(f"Spec change: #{ref.spec_change}")
    lines.extend(f"Capability: {name}" for name in ref.capabilities)
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
    capabilities: list[str] = []
    for line in lines[start:]:
        key, sep, value = line.partition(":")
        if not sep or key in fields:
            return None
        if key == "Capability":
            capabilities.append(value.strip())
        else:
            fields[key] = value.strip()

    requirement = fields.pop("Requirement", None)
    spec_change = fields.pop("Spec change", None)
    spec_gap = fields.pop("Spec gap", None)
    if (
        fields
        or not capabilities
        or not all(capabilities)
        or (spec_change is None) == (spec_gap is None)
    ):
        return None
    names = tuple(capabilities)
    if spec_gap is not None:
        return SpecReference(None, names, requirement) if spec_gap == "yes" else None
    match = _SPEC_CHANGE.fullmatch(spec_change or "")
    return SpecReference(int(match.group(1)), names, requirement) if match else None
