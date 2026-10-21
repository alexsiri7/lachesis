"""Reading what the web view marks with data- attributes out of its rendered HTML."""

from __future__ import annotations

import re
from html.parser import HTMLParser


class _Elements(HTMLParser):
    """Every element's attributes, in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append({"tag": tag, **{k: v or "" for k, v in attrs}})


def elements_in(html: str) -> list[dict[str, str]]:
    """Every element in *html* as its attributes, with its tag under ``tag``."""
    parser = _Elements()
    parser.feed(html)
    return parser.elements


def section_of(html: str, section_id: str) -> str:
    """The inside of the ``<section>`` with id *section_id*, which must be on the page."""
    match = re.search(rf'<section[^>]*\bid="{section_id}"[^>]*>(.*?)</section>', html, re.DOTALL)
    assert match is not None, f"no section {section_id!r} on the page"
    return match.group(1)


def with_attribute(found: list[dict[str, str]], attribute: str) -> list[dict[str, str]]:
    """The elements of *found* that carry *attribute*."""
    return [element for element in found if attribute in element]
