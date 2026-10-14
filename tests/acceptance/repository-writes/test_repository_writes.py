"""Repository writes: a requirement file is one stable thing whichever branch it sits on.

These scenarios turn on what GitHub holds between requests — which branch has which version
of a file, which pull requests are open, what merging one does — so GitHub here is a small
model of one repository rather than canned answers.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from typing import Any

import pytest
import yaml

from harness import Lachesis, find_matching, scenario
from harness.github import NOT_FOUND, RecordedRequest

REPO = "owner/widgets"
PROTECTED = {"message": "Changes must be made through a pull request.", "status": "409"}
LINKED = {"number": 7, "title": "Count the widgets"}


def _sha(content: str) -> str:
    return hashlib.sha1(content.encode()).hexdigest()


def _requirement(id: str, title: str) -> str:
    return f"""\
---
id: '{id}'
title: {title}
status: draft
github_issue: null
created: '2026-10-01'
updated: '2026-10-01'
---
## Why

Nobody knows how many widgets there are.

## What

Count them.

## Issues

_None yet._
"""


class Repository:
    """GitHub as REPO: the files on each branch, and its open pull requests.

    A merged pull request's branch is deleted, as ``delete_branch_on_merge`` does.
    """

    def __init__(self, lachesis: Lachesis, *, protected: bool) -> None:
        self.protected = protected
        self.branches: dict[str, dict[str, str]] = {"main": {}}
        self.pulls: list[dict[str, Any]] = []
        self.other_writer: Callable[[str], str] | None = None
        self._bases: dict[str, dict[str, str]] = {}
        self._opened = 0
        lachesis.github.serve(f"/repos/{REPO}", self._answer)
        lachesis.github.on("POST", "/graphql", json={"data": {}})

    def holds(self, path: str, content: str, *, branch: str = "main") -> None:
        self.branches.setdefault(branch, dict(self.branches["main"]))[path] = content

    def open_pull(self, branch: str) -> dict[str, Any]:
        self._bases.setdefault(branch, dict(self.branches["main"]))
        self.branches.setdefault(branch, dict(self.branches["main"]))
        self._opened += 1
        number = 100 + self._opened
        pull = {
            "number": number,
            "node_id": f"PR_{number}",
            "html_url": f"https://github.com/{REPO}/pull/{number}",
            "head": {"ref": branch},
            "created_at": "2026-10-12T09:00:00Z",
        }
        self.pulls.append(pull)
        return pull

    def merge(self, pull: dict[str, Any]) -> None:
        branch = pull["head"]["ref"]
        base = self._bases.pop(branch)
        for path, content in self.branches.pop(branch).items():
            if base.get(path) != content:
                self.branches["main"][path] = content
        self.pulls.remove(pull)

    def _answer(self, request: RecordedRequest) -> tuple[int, Any]:
        rest = request.path.removeprefix(f"/repos/{REPO}")
        match request.method, rest.split("/")[1:3]:
            case "GET", []:
                return 200, {
                    "full_name": REPO,
                    "default_branch": "main",
                    "allow_auto_merge": True,
                    "allow_squash_merge": True,
                    "allow_merge_commit": False,
                    "delete_branch_on_merge": True,
                }
            case "GET", ["contents", *_]:
                return self._read(rest.removeprefix("/contents/"), request.params)
            case "PUT", ["contents", *_]:
                return self._write(rest.removeprefix("/contents/"), request.body)
            case "GET", ["git", "ref"]:
                branch = rest.removeprefix("/git/ref/heads/")
                if branch not in self.branches:
                    return 404, NOT_FOUND
                return 200, {"object": {"sha": f"head-of-{branch}"}}
            case "POST", ["git", "refs"]:
                branch = request.body["ref"].removeprefix("refs/heads/")
                if branch in self.branches:
                    return 422, {"message": "Reference already exists"}
                self._bases[branch] = dict(self.branches["main"])
                self.branches[branch] = dict(self.branches["main"])
                return 201, {"ref": request.body["ref"]}
            case "GET", ["pulls"]:
                return 200, self.pulls
            case "POST", ["pulls"]:
                return 201, self.open_pull(request.body["head"])
        return 404, NOT_FOUND

    def _read(self, path: str, params: dict[str, str]) -> tuple[int, Any]:
        files = self.branches.get(params.get("ref", "main"))
        if files is None:
            return 404, NOT_FOUND
        if path in files:
            return 200, {
                "type": "file",
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "sha": _sha(files[path]),
                "encoding": "base64",
                "content": base64.b64encode(files[path].encode()).decode(),
            }
        listed = [
            {"type": "file", "name": name, "path": f"{path}/{name}", "sha": _sha(content)}
            for full, content in sorted(files.items())
            if full.startswith(f"{path}/") and "/" not in (name := full.removeprefix(f"{path}/"))
        ]
        return (200, listed) if listed else (404, NOT_FOUND)

    def _write(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        branch = body.get("branch", "main")
        files = self.branches.get(branch)
        if files is None:
            return 404, {"message": f"Branch {branch} not found"}
        if branch == "main" and self.protected:
            return 409, PROTECTED
        if self.other_writer is not None and path in files:
            files[path] = self.other_writer(files[path])
        current = files.get(path)
        if current is None and "sha" in body:
            return 422, {"message": f"{path} does not exist"}
        if current is not None and body.get("sha") != _sha(current):
            return 409, {"message": f"{path} does not match {body.get('sha')}"}
        files[path] = base64.b64decode(body["content"]).decode()
        return 200, {"content": {"path": path, "sha": _sha(files[path])}}


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


async def _registered(lachesis: Lachesis, *, protected: bool = False) -> Repository:
    repo = Repository(lachesis, protected=protected)
    await _ok(lachesis, "register_repo", {"repo": REPO})
    return repo


async def _create(lachesis: Lachesis, title: str = "Widgets are counted") -> Any:
    return await _ok(
        lachesis,
        "create_requirement",
        {"repo": REPO, "title": title, "why": "Nobody knows.", "what": "Count them."},
    )


async def _read(lachesis: Lachesis, id: str) -> Any:
    return await _ok(lachesis, "get_requirement", {"repo": REPO, "id": id})


def _identifies(answer: Any, pull: dict[str, Any]) -> bool:
    """Whether *answer* names *pull* anywhere, by its number or by its address."""
    return bool(
        find_matching(
            answer, lambda d: pull["number"] in d.values() or pull["html_url"] in d.values()
        )
    )


def _frontmatter(content: str) -> dict[str, Any]:
    return yaml.safe_load(content.split("---\n")[1])


@scenario("repository-writes", "Restart loses nothing")
async def test_restart_loses_nothing(lachesis: Lachesis) -> None:
    await _registered(lachesis)
    created = await _create(lachesis)
    before = await _read(lachesis, created["id"])

    await lachesis.restart()

    assert await _read(lachesis, created["id"]) == before
    assert "Widgets are counted" in before["content"]


@scenario("repository-writes", "Protected default branch")
async def test_protected_default_branch(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis, protected=True)

    created = await _create(lachesis)

    assert not repo.branches["main"]
    [pull] = repo.pulls
    branch = pull["head"]["ref"]
    assert branch.startswith("lachesis/"), branch
    assert len(repo.branches[branch]) == 1, repo.branches[branch]
    [automerge] = lachesis.github.automerge_requests()
    assert automerge["variables"]["pullRequestId"] == pull["node_id"]
    assert _identifies(created, pull), created


@scenario("repository-writes", "Create then link immediately")
async def test_create_then_link_immediately(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis, protected=True)
    created = await _create(lachesis)
    [pull] = repo.pulls

    await _ok(lachesis, "link_issues", {"repo": REPO, "id": created["id"], "issues": [LINKED]})

    read = await _read(lachesis, created["id"])
    assert f"#{LINKED['number']}" in read["content"], read["content"]
    assert read["pr"]["number"] == pull["number"], read


@scenario("repository-writes", "After merge")
async def test_after_merge(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis, protected=True)
    created = await _create(lachesis)
    [pull] = repo.pulls
    repo.merge(pull)

    read = await _read(lachesis, created["id"])

    assert "Widgets are counted" in read["content"]
    assert read.get("pr") is None, read
    assert not _identifies(read, pull), read


@scenario("repository-writes", "Merge races the write")
async def test_merge_races_the_write(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis)
    path = "requirements/001-widgets-are-counted.md"
    branch = "lachesis/req-001-widgets-are-counted-20261012T090000Z"
    repo.holds(path, _requirement("001", "Widgets are counted"), branch=branch)
    pull = repo.open_pull(branch)
    assert (await _read(lachesis, "001"))["pr"]["number"] == pull["number"]
    repo.merge(pull)

    await _ok(lachesis, "link_issues", {"repo": REPO, "id": "001", "issues": [LINKED]})

    assert f"#{LINKED['number']}" in repo.branches["main"][path]
    assert not repo.pulls


@scenario("repository-writes", "Tenth write")
async def test_tenth_write(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis, protected=True)
    id = (await _create(lachesis))["id"]

    async def update(n: int) -> Any:
        return await lachesis.call_tool(
            "update_requirement", {"repo": REPO, "id": id, "fields": {"what": f"Take {n}."}}
        )

    for n in range(2, 6):
        assert not (await update(n)).is_error
    repo.merge(repo.pulls[0])
    repo.protected = False
    for n in range(6, 10):
        assert not (await update(n)).is_error

    tenth = await update(10)

    assert not tenth.is_error, tenth.text
    assert "Take 10." in (await _read(lachesis, id))["content"]


@pytest.mark.xfail(strict=True, reason="spec gap")
@scenario("repository-writes", "Concurrent edit")
async def test_concurrent_edit(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis)
    repo.holds(
        "requirements/001-widgets-are-counted.md", _requirement("001", "Widgets are counted")
    )
    repo.other_writer = lambda content: content + "\nCounted by hand.\n"

    result = await lachesis.call_tool(
        "update_requirement", {"repo": REPO, "id": "001", "fields": {"what": "Count them twice."}}
    )

    assert result.is_error, result.text
    assert "changed" in result.text.lower(), result.text
    assert "Counted by hand." in result.text, result.text
    assert "409" not in result.text, result.text


@scenario("repository-writes", "Several created before any merge")
async def test_several_created_before_any_merge(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis, protected=True)
    for id, title in (("028", "Widgets are listed"), ("029", "Widgets are sorted")):
        repo.holds(
            f"requirements/{id}-{title.lower().replace(' ', '-')}.md", _requirement(id, title)
        )

    created = [await _create(lachesis, title) for title in ("Counted", "Weighed", "Painted")]

    assert [c["id"] for c in created] == ["030", "031", "032"]


@scenario("repository-writes", "Identifier survives a round trip")
async def test_identifier_survives_a_round_trip(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis)
    repo.holds(
        "requirements/030-widgets-are-counted.md", _requirement("030", "Widgets are counted")
    )

    await _read(lachesis, "030")
    updated = await _ok(
        lachesis,
        "update_requirement",
        {"repo": REPO, "id": "030", "fields": {"title": "Widgets are counted daily"}},
    )
    read = await _read(lachesis, "030")

    assert _frontmatter(updated["content"])["id"] == "030"
    assert _frontmatter(read["content"])["title"] == "Widgets are counted daily"
    assert _frontmatter(read["content"])["id"] == "030"
