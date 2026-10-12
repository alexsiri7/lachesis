"""Searching a tool's nested JSON answer for the entries a test cares about."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def find_matching(value: Any, predicate: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    """Every dict nested anywhere in *value* that satisfies *predicate*."""
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if predicate(value):
            found.append(value)
        children = list(value.values())
    elif isinstance(value, list):
        children = value
    else:
        return found
    for child in children:
        found += find_matching(child, predicate)
    return found
