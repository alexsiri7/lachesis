"""Repository contents: read-only access to the files of registered repositories.

The GitHub stub does not tell refs apart, so a read at a branch is checked by what the
branch serves and by the ref the read asked GitHub for.
"""

from __future__ import annotations

import base64
from typing import Any

from harness import Lachesis, scenario

REPO = "owner/widgets"
BACKLOG = "src/backlog.py"
CONTENTS = f"/repos/{REPO}/contents"
BRANCH = "faster-backlog"

DEFAULT_SOURCE = "def backlog():\n    return sorted(open_issues())\n"
BRANCH_SOURCE = "def backlog():\n    return sorted(open_issues(), key=priority)\n"


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


def _file(path: str, source: str) -> dict[str, Any]:
    data = source.encode()
    return {
        "type": "file",
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "size": len(data),
        "encoding": "base64",
        "content": base64.b64encode(data).decode(),
        "download_url": f"https://raw.githubusercontent.com/{REPO}/main/{path}",
    }


def _entry(path: str, kind: str) -> dict[str, Any]:
    url = f"https://raw.githubusercontent.com/{REPO}/main/{path}" if kind == "file" else None
    return {"type": kind, "name": path.rsplit("/", 1)[-1], "path": path, "download_url": url}


@scenario("repository-contents", "Reading source for an audit")
async def test_reading_source_for_an_audit(lachesis: Lachesis) -> None:
    await _ok(lachesis, "register_repo", {"repo": REPO})
    lachesis.github.on("GET", f"{CONTENTS}/{BACKLOG}", json=_file(BACKLOG, DEFAULT_SOURCE))

    read = await _ok(lachesis, "read_file", {"repo": REPO, "path": BACKLOG})

    assert read["content"] == DEFAULT_SOURCE
    [request] = lachesis.github.requests_to("GET", f"{CONTENTS}/{BACKLOG}")
    assert "ref" not in request.params


@scenario("repository-contents", "Reading a pull request's version")
async def test_reading_a_pull_requests_version(lachesis: Lachesis) -> None:
    await _ok(lachesis, "register_repo", {"repo": REPO})
    lachesis.github.on("GET", f"{CONTENTS}/{BACKLOG}", json=_file(BACKLOG, BRANCH_SOURCE))

    read = await _ok(lachesis, "read_file", {"repo": REPO, "path": BACKLOG, "ref": BRANCH})

    assert read["content"] == BRANCH_SOURCE
    [request] = lachesis.github.requests_to("GET", f"{CONTENTS}/{BACKLOG}")
    assert request.params.get("ref") == BRANCH


@scenario("repository-contents", "Finding the tests")
async def test_finding_the_tests(lachesis: Lachesis) -> None:
    await _ok(lachesis, "register_repo", {"repo": REPO})
    tests = {
        "tests/conftest.py": "file",
        "tests/test_backlog.py": "file",
        "tests/acceptance": "dir",
    }
    lachesis.github.on(
        "GET", f"{CONTENTS}/tests", json=[_entry(path, kind) for path, kind in tests.items()]
    )

    listed = await _ok(lachesis, "list_directory", {"repo": REPO, "path": "tests"})

    assert {entry["path"]: entry["kind"] for entry in listed["entries"]} == tests


@scenario("repository-contents", "Unregistered repository")
async def test_unregistered_repository(lachesis: Lachesis) -> None:
    lachesis.github.on("GET", f"{CONTENTS}/{BACKLOG}", json=_file(BACKLOG, DEFAULT_SOURCE))

    refused = await lachesis.call_tool("read_file", {"repo": REPO, "path": BACKLOG})

    assert refused.is_error
    assert "not registered" in refused.text
    assert lachesis.github.requests_to("GET", f"{CONTENTS}/{BACKLOG}") == []
