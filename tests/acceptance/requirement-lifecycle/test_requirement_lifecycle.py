"""Requirement lifecycle: what a requirement file holds and how it moves through its life.

Each repository here is a small model of its default branch, so a requirement written by
one call is the one the next call reads.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest
import yaml

from harness import Lachesis, scenario
from harness.github import NOT_FOUND, RecordedRequest

REPO = "owner/widgets"
OTHER_REPO = "owner/gadgets"
ISSUE_12 = {"number": 12, "title": "Count the widgets"}
ISSUE_13 = {"number": 13, "title": "Show the count"}


def _sha(content: str) -> str:
    return hashlib.sha1(content.encode()).hexdigest()


def _requirement(id: str, title: str, *, status: str) -> str:
    return f"""\
---
id: '{id}'
title: {title}
status: {status}
github_issue: 12
created: '2026-10-01'
updated: '2026-10-01'
---
## Why

Nobody knows how many widgets there are.

## What

Count them.

## Issues

- #12 — Count the widgets
"""


def _path(id: str, title: str) -> str:
    return f"requirements/{id}-{title.lower().replace(' ', '-')}.md"


class Repository:
    """GitHub as one repository with an unprotected default branch and no pull requests."""

    def __init__(self, lachesis: Lachesis, name: str) -> None:
        self.name = name
        self.files: dict[str, str] = {}
        lachesis.github.serve(f"/repos/{name}", self._answer)

    def holds(self, path: str, content: str) -> None:
        self.files[path] = content

    def _answer(self, request: RecordedRequest) -> tuple[int, Any]:
        rest = request.path.removeprefix(f"/repos/{self.name}")
        path = rest.removeprefix("/contents/")
        match request.method, rest.split("/")[1:2]:
            case "GET", []:
                return 200, {"full_name": self.name, "default_branch": "main"}
            case "GET", ["contents"]:
                return self._read(path)
            case "PUT", ["contents"]:
                return self._write(path, request.body)
            case "DELETE", ["contents"]:
                if request.body.get("sha") != _sha(self.files.get(path, "")):
                    return 409, {"message": f"{path} does not match"}
                del self.files[path]
                return 200, {"content": None}
            case "GET", ["pulls"]:
                return 200, []
        return 404, NOT_FOUND

    def _read(self, path: str) -> tuple[int, Any]:
        if path in self.files:
            return 200, {
                "type": "file",
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "sha": _sha(self.files[path]),
                "encoding": "base64",
                "content": base64.b64encode(self.files[path].encode()).decode(),
            }
        children = {
            full.removeprefix(f"{path}/").split("/")[0]: full
            for full in sorted(self.files)
            if full.startswith(f"{path}/")
        }
        listed = [
            {"type": "file", "name": name, "path": full, "sha": _sha(self.files[full])}
            if full == f"{path}/{name}"
            else {"type": "dir", "name": name, "path": f"{path}/{name}"}
            for name, full in children.items()
        ]
        return (200, listed) if listed else (404, NOT_FOUND)

    def _write(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        current = self.files.get(path)
        if current is not None and body.get("sha") != _sha(current):
            return 409, {"message": f"{path} does not match {body.get('sha')}"}
        self.files[path] = base64.b64decode(body["content"]).decode()
        return 200, {"content": {"path": path, "sha": _sha(self.files[path])}}


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


async def _registered(lachesis: Lachesis, name: str = REPO) -> Repository:
    lachesis.github.on("POST", "/graphql", json={"data": {}})
    repo = Repository(lachesis, name)
    await _ok(lachesis, "register_repo", {"repo": name})
    return repo


async def _create(lachesis: Lachesis) -> str:
    created = await _ok(
        lachesis,
        "create_requirement",
        {"repo": REPO, "title": "Widgets are counted", "why": "Nobody knows.", "what": "Count."},
    )
    return created["id"]


async def _link(lachesis: Lachesis, id: str, *issues: dict[str, Any]) -> None:
    await _ok(lachesis, "link_issues", {"repo": REPO, "id": id, "issues": list(issues)})


async def _update(lachesis: Lachesis, id: str, fields: dict[str, Any]) -> Any:
    return await lachesis.call_tool(
        "update_requirement", {"repo": REPO, "id": id, "fields": fields}
    )


async def _content(lachesis: Lachesis, id: str, repo: str = REPO) -> str:
    return (await _ok(lachesis, "get_requirement", {"repo": repo, "id": id}))["content"]


def _frontmatter(content: str) -> dict[str, Any]:
    return yaml.safe_load(content.split("---\n")[1])


def _issues_section(content: str) -> str:
    return content.split("## Issues", 1)[1]


async def _listed(lachesis: Lachesis, repo: str = REPO, **arguments: Any) -> dict[str, Any]:
    entries = await _ok(lachesis, "list_requirements", {"repo": repo, **arguments})
    return {entry["id"]: entry for entry in entries}


@scenario("requirement-lifecycle", "Creating a requirement")
async def test_creating_a_requirement(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis)

    id = await _create(lachesis)

    [stored] = repo.files.values()
    assert stored == await _content(lachesis, id)
    assert _frontmatter(stored)["status"] == "draft"
    assert "#" not in _issues_section(stored), stored
    assert (await _listed(lachesis))[id]["status"] == "draft"


@scenario("requirement-lifecycle", "First link")
async def test_first_link(lachesis: Lachesis) -> None:
    await _registered(lachesis)
    id = await _create(lachesis)

    await _link(lachesis, id, ISSUE_12, ISSUE_13)

    content = await _content(lachesis, id)
    assert _frontmatter(content)["status"] == "idea"
    issues = _issues_section(content)
    for issue in (ISSUE_12, ISSUE_13):
        line = next(line for line in issues.splitlines() if f"#{issue['number']}" in line)
        assert issue["title"] in line, issues


@scenario("requirement-lifecycle", "Link the same issue twice")
async def test_link_the_same_issue_twice(lachesis: Lachesis) -> None:
    await _registered(lachesis)
    id = await _create(lachesis)
    await _link(lachesis, id, ISSUE_12)

    await _link(lachesis, id, ISSUE_12, ISSUE_13)

    issues = _issues_section(await _content(lachesis, id))
    assert issues.count("#12") == 1, issues
    assert issues.count("#13") == 1, issues


@pytest.mark.xfail(strict=True, reason="spec gap")
@scenario("requirement-lifecycle", "Extending the what after linking")
async def test_extending_the_what_after_linking(lachesis: Lachesis) -> None:
    await _registered(lachesis)
    id = await _create(lachesis)
    await _link(lachesis, id, ISSUE_12)

    assert not (await _update(lachesis, id, {"what": "Count them, and show the count."})).is_error
    await _link(lachesis, id, ISSUE_13)

    content = await _content(lachesis, id)
    assert "Count them, and show the count." in content, content
    issues = _issues_section(content)
    assert "#12" in issues, issues
    assert "#13" in issues, issues


@scenario("requirement-lifecycle", "Variant spelling in an old record")
async def test_variant_spelling_in_an_old_record(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis)
    repo.holds(
        _path("007", "Widgets are counted"),
        _requirement("007", "Widgets are counted", status="in_progress"),
    )

    listed = await _listed(lachesis)
    dashboard = await _ok(lachesis, "get_dashboard", {})

    assert listed["007"]["status"] == "in-progress"
    assert dashboard["summary"]["by_status"] == {"in-progress": 1}


@scenario("requirement-lifecycle", "Cancel without a reason")
async def test_cancel_without_a_reason(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis)
    id = await _create(lachesis)
    before = dict(repo.files)

    result = await _update(lachesis, id, {"status": "cancelled"})

    assert result.is_error, result.text
    assert "reason" in result.text.lower(), result.text
    assert repo.files == before


@scenario("requirement-lifecycle", "Supersede across repositories")
async def test_supersede_across_repositories(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis)
    await _registered(lachesis, OTHER_REPO)
    repo.holds(
        _path("012", "Widgets are counted"),
        _requirement("012", "Widgets are counted", status="planned"),
    )

    superseded = await _update(
        lachesis,
        "012",
        {"status": "superseded", "superseded_by": {"id": "030", "repo": OTHER_REPO}},
    )

    assert not superseded.is_error, superseded.text
    assert "012" not in await _listed(lachesis)
    inactive = (await _listed(lachesis, include_inactive=True))["012"]
    assert inactive["superseded_by"] == {"id": "030", "repo": OTHER_REPO}


@scenario("requirement-lifecycle", "Superseded by a spec")
async def test_superseded_by_a_spec(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis)
    old = {"003": "Widgets are counted", "004": "Widgets are weighed"}
    for id, title in old.items():
        repo.holds(_path(id, title), _requirement(id, title, status="planned"))
    repo.holds("openspec/specs/widgets/spec.md", "# Widgets\n")
    spec_change = 40

    for id in old:
        superseded = await _update(
            lachesis,
            id,
            {"status": "superseded", "superseded_by": {"spec_change": spec_change, "repo": REPO}},
        )
        assert not superseded.is_error, superseded.text

    inactive = await _listed(lachesis, include_inactive=True)
    for id in old:
        assert inactive[id]["superseded_by"] == {"spec_change": spec_change, "repo": REPO}


@scenario("requirement-lifecycle", "Delete a done requirement")
async def test_delete_a_done_requirement(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis)
    repo.holds(
        _path("009", "Widgets are counted"),
        _requirement("009", "Widgets are counted", status="done"),
    )
    before = dict(repo.files)

    result = await lachesis.call_tool("delete_requirement", {"repo": REPO, "id": "009"})

    assert result.is_error, result.text
    assert repo.files == before
    assert (await _listed(lachesis))["009"]["status"] == "done"
