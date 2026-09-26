"""MCP tool implementations for reading and changing a repository's OpenSpec specs."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

import github

from .requirements import _MAX_WRITE_ATTEMPTS, _open_pull_request, _resolve_repo

logger = logging.getLogger(__name__)

SPECS_DIR = "openspec/specs"
SPEC_CHANGE_LABEL = "spec-change"

_CAPABILITY_NAME = re.compile(r"\w[\w.-]*")
_PURPOSE = re.compile(r"^## Purpose[ \t]*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
_REQUIREMENT = re.compile(r"^### Requirement:", re.MULTILINE)
_TITLE = re.compile(r"^# (.*?)[ \t]*$", re.MULTILINE)
_REQUIREMENTS_SECTION = re.compile(
    r"^## Requirements[ \t]*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL
)
_REQUIREMENT_BLOCK = re.compile(
    r"^### Requirement:[ \t]*(.*?)[ \t]*$(.*?)(?=^### |\Z)", re.MULTILINE | re.DOTALL
)
_SUBHEADING = re.compile(r"^#### ", re.MULTILINE)
_SCENARIO = re.compile(r"^#### Scenario:", re.MULTILINE)
_NORMATIVE = re.compile(r"\b(?:SHALL|MUST)\b")


async def uses_specs(repo: str) -> bool:
    """Return whether *repo*'s default branch contains ``openspec/specs/``."""
    return bool(await github.list_files(repo, SPECS_DIR))


def _not_using_specs(repo: str) -> dict[str, Any]:
    return {
        "repo": repo,
        "uses_specs": False,
        "message": (
            f"{repo} does not use specs: its default branch has no {SPECS_DIR}/ directory."
        ),
    }


def _spec_path(capability: str) -> str:
    return f"{SPECS_DIR}/{capability}/spec.md"


def _summarize(capability: str, spec: str) -> dict[str, Any]:
    purpose = _PURPOSE.search(spec)
    return {
        "name": capability,
        "purpose": purpose.group(1).strip() if purpose else "",
        "requirement_count": len(_REQUIREMENT.findall(spec)),
    }


async def list_capabilities(repo: str) -> dict[str, Any]:
    """List the capabilities specified on *repo*'s default branch.

    Returns ``{"repo", "uses_specs": True, "capabilities": [{"name", "purpose",
    "requirement_count"}, ...]}``, or ``{"repo", "uses_specs": False, "message"}``
    when the repository does not use specs.
    """
    repo = await _resolve_repo(repo)
    entries = await github.list_files(repo, SPECS_DIR)
    if not entries:
        return _not_using_specs(repo)
    names = sorted(e["name"] for e in entries if e.get("type") == "dir")
    specs = await asyncio.gather(*(github.get_file(repo, _spec_path(n)) for n in names))
    return {
        "repo": repo,
        "uses_specs": True,
        "capabilities": [
            _summarize(name, content) for name, (content, _sha) in zip(names, specs, strict=True)
        ],
    }


async def _spec_changes_touching(repo: str, path: str) -> list[dict[str, Any]]:
    pulls = await github.list_labelled_prs(repo, SPEC_CHANGE_LABEL)
    changed = await asyncio.gather(
        *(github.list_pull_request_files(repo, pr["number"]) for pr in pulls)
    )
    return [pr for pr, files in zip(pulls, changed, strict=True) if path in files]


async def get_capability(repo: str, capability: str) -> dict[str, Any]:
    """Return one capability's spec as it stands on *repo*'s default branch.

    Returns ``{"repo", "uses_specs": True, "name", "spec", "spec_changes": [{"number",
    "title", "url"}, ...]}``, where ``spec_changes`` are the open pull requests labelled
    ``spec-change`` that touch the capability's file, or ``{"repo", "uses_specs": False,
    "message"}`` when the repository does not use specs.
    """
    if not _CAPABILITY_NAME.fullmatch(capability):
        raise ValueError(f"Invalid capability name {capability!r}")
    repo = await _resolve_repo(repo)
    if not await uses_specs(repo):
        return _not_using_specs(repo)
    path = _spec_path(capability)
    try:
        spec, _sha = await github.get_file(repo, path)
    except FileNotFoundError:
        raise FileNotFoundError(f"Capability {capability!r} not found in {repo!r}") from None
    return {
        "repo": repo,
        "uses_specs": True,
        "name": capability,
        "spec": spec,
        "spec_changes": await _spec_changes_touching(repo, path),
    }


def spec_problems(capability: str, spec: str) -> list[str]:
    """Return every format problem in *spec*, each naming *capability* and any requirement.

    Mirrors the rules ``openspec validate --strict`` enforces for a title, the Purpose
    and Requirements sections, and each requirement's SHALL/MUST statement and scenarios.
    A requirement's statement is its text before its first ``####`` heading: OpenSpec
    counts neither the requirement heading nor its scenarios.
    """
    problems: list[str] = []
    title = _TITLE.search(spec)
    if title is None:
        problems.append(f"{capability}: missing a '# Title' heading")
    elif title.group(1) == "Requirements":
        problems.append(
            f"{capability}: the title must not be exactly 'Requirements'; OpenSpec mistakes "
            "it for the Requirements section heading"
        )
    if not _PURPOSE.search(spec):
        problems.append(f"{capability}: missing a '## Purpose' section")
    section = _REQUIREMENTS_SECTION.search(spec)
    if section is None:
        problems.append(f"{capability}: missing a '## Requirements' section")
        return problems
    for requirement in _REQUIREMENT_BLOCK.finditer(section.group(1)):
        name, body = requirement.group(1), requirement.group(2)
        statement = _SUBHEADING.split(body, maxsplit=1)[0]
        if not _NORMATIVE.search(statement):
            problems.append(
                f"{capability}: requirement {name!r} must say SHALL or MUST in its text, "
                "not only in its heading or scenarios"
            )
        if not _SCENARIO.search(body):
            problems.append(f"{capability}: requirement {name!r} has no '#### Scenario:' heading")
    return problems


def _check_capabilities(capabilities: dict[str, str | None]) -> None:
    """Refuse a spec change listing every malformed capability name and content at once."""
    problems: list[str] = []
    for name, spec in capabilities.items():
        if not _CAPABILITY_NAME.fullmatch(name):
            problems.append(f"Invalid capability name {name!r}")
        elif spec is not None:
            problems.extend(spec_problems(name, spec))
    if problems:
        raise ValueError(
            "Spec change refused; fix every problem and retry:\n"
            + "\n".join(f"- {problem}" for problem in problems)
        )


async def _write_capability(
    repo: str, branch: str, capability: str, spec: str | None, message: str
) -> None:
    """Make *capability*'s file on *branch* hold *spec*, deleting it when *spec* is ``None``.

    The file's sha is read immediately before each write, and a 409 from a branch
    that moved in between costs a fresh read and another attempt.
    """
    path = _spec_path(capability)
    for attempt in range(1, _MAX_WRITE_ATTEMPTS + 1):
        try:
            current, sha = await github.get_file(repo, path, branch=branch)
        except FileNotFoundError:
            current, sha = None, None
        try:
            if spec is None:
                if sha is not None:
                    await github.delete_file(repo, path, sha, message, branch=branch)
            elif sha is None:
                await github.create_file(repo, path, spec, message, branch=branch)
            elif current != spec:
                await github.update_file(repo, path, spec, sha, message, branch=branch)
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            logger.warning(
                "spec write conflicted repo=%s path=%s branch=%s attempt=%d/%d",
                repo,
                path,
                branch,
                attempt,
                _MAX_WRITE_ATTEMPTS,
            )
    raise RuntimeError(
        f"Could not write {path} in {repo!r}: GitHub reported a conflict on "
        f"{_MAX_WRITE_ATTEMPTS} consecutive attempts, each against a freshly read version "
        f"of the file on branch {branch!r}. Something else is changing that file "
        "concurrently; retry once it settles."
    )


async def propose_spec_change(
    repo: str, title: str, why: str, capabilities: dict[str, str | None]
) -> dict[str, Any]:
    """Open a pull request labelled ``spec-change`` that edits one or more capabilities.

    *capabilities* maps each capability name to its complete new ``spec.md`` content,
    or to ``None`` to remove the capability.  *why* becomes the pull request
    description.  Every capability is format-checked before anything is written.

    Returns ``{"repo", "number", "url"}`` of the pull request.
    """
    if not capabilities:
        raise ValueError("A spec change must add, edit or remove at least one capability")
    _check_capabilities(capabilities)
    repo = await _resolve_repo(repo)
    if not await uses_specs(repo):
        raise ValueError(
            _not_using_specs(repo)["message"] + " Spec changes need a repository that uses specs."
        )
    slug = re.sub(r"[^a-z0-9-]+", "", title.lower().replace(" ", "-")).strip("-")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    branch = f"spec-change/{slug}-{stamp}"

    async def commit() -> None:
        for name, spec in capabilities.items():
            await _write_capability(repo, branch, name, spec, f"spec: {title}")

    _, pr = await _open_pull_request(repo, branch, title=title, body=why, commit=commit)
    try:
        await github.add_labels(repo, pr["number"], [SPEC_CHANGE_LABEL])
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Opened spec change #{pr['number']} ({pr['url']}) but could not label it "
            f"{SPEC_CHANGE_LABEL!r} ({exc!r}). Add the label by hand: until it has it, the "
            "pull request is not listed against the capabilities it touches."
        ) from exc
    return {"repo": repo, "number": pr["number"], "url": pr["url"]}


async def update_spec_change(
    repo: str,
    pr_number: int,
    why: str | None = None,
    capabilities: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Edit the open spec change *pr_number*.

    *capabilities* maps each capability name to its complete new ``spec.md`` content,
    or to ``None`` to remove the capability, on the pull request's branch; capabilities
    not named are left as they are.  *why*, when given, replaces the description.
    Refused once the pull request has merged or closed.

    Returns ``{"repo", "number", "url"}`` of the pull request.
    """
    if why is None and not capabilities:
        raise ValueError("Nothing to update: pass a new why, capabilities to change, or both")
    if capabilities:
        _check_capabilities(capabilities)
    repo = await _resolve_repo(repo)
    pull = await github.get_pull_request(repo, pr_number)
    if pull is None:
        raise FileNotFoundError(f"Pull request #{pr_number} not found in {repo!r}")
    if SPEC_CHANGE_LABEL not in pull["labels"]:
        raise ValueError(
            f"Pull request #{pr_number} in {repo!r} is not a spec change: it is not "
            f"labelled {SPEC_CHANGE_LABEL!r}."
        )
    if pull["state"] != "open":
        outcome = "merged" if pull["merged"] else "been closed"
        raise ValueError(
            f"Spec change #{pr_number} has {outcome} and can no longer be edited. "
            "Propose a new spec change instead."
        )
    for name, spec in (capabilities or {}).items():
        await _write_capability(repo, pull["head_ref"], name, spec, f"spec: {pull['title']}")
    if why is not None:
        await github.update_issue(repo, pr_number, {"body": why})
    return {"repo": repo, "number": pr_number, "url": pull["url"]}
