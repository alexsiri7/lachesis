"""MCP tool implementations for reading and changing a repository's OpenSpec specs."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, NamedTuple

import httpx

import github
import landing
from derive import (
    ActivityFailure,
    IssueFacts,
    SpecChangeState,
    derive_spec_change,
    spec_change_dropped,
)
from spec_reference import SpecReference, parse_reference

from .requirements import (
    _MAX_WRITE_ATTEMPTS,
    AUTHOR_REQUEST_LABEL,
    SPEC_GAP_LABEL,
    _open_pull_request,
    _resolve_repo,
)

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
_FENCE_OPENING = re.compile(r" {0,3}(?:(`{3,})[^`]*|(~{3,}).*)")
_FENCE_CLOSING = re.compile(r" {0,3}(`{3,}|~{3,})[ \t]*")
_SPEC_PATH = re.compile(rf"{SPECS_DIR}/({_CAPABILITY_NAME.pattern})/spec\.md")

RequirementChange = Literal["added", "changed"]


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


def _outside_code_blocks(spec: str) -> str:
    """Return *spec* with every fenced code block's lines blanked out.

    Fences open and close by CommonMark's rules: at most three spaces of indentation, no
    backtick in a backtick fence's info string, and a closing fence of the same character, at
    least as long, followed only by spaces or tabs. An unclosed fence runs to the end of the
    document.
    """
    lines = spec.split("\n")
    fence = ""
    for i, line in enumerate(lines):
        if fence:
            closing = _FENCE_CLOSING.fullmatch(line)
            if closing and closing.group(1).startswith(fence):
                fence = ""
            lines[i] = ""
        elif opening := _FENCE_OPENING.fullmatch(line):
            fence = opening.group(1) or opening.group(2)
            lines[i] = ""
    return "\n".join(lines)


def spec_problems(capability: str, spec: str) -> list[str]:
    """Return every format problem in *spec*, each naming *capability* and any requirement.

    Mirrors the rules ``openspec validate --strict`` enforces for a title, the Purpose
    and Requirements sections, and each requirement's SHALL/MUST statement and scenarios.
    A requirement's statement is its text before its first ``####`` heading: OpenSpec
    counts neither the requirement heading nor its scenarios.  Fenced code blocks are
    ignored, so an example inside one cannot stand in for a missing element.
    """
    spec = _outside_code_blocks(spec)
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


def requirement_texts(spec: str) -> dict[str, str]:
    """Map each requirement in *spec*'s Requirements section to its text, in document order.

    Trailing whitespace on a line and blank lines around the text are not part of it.  A
    heading inside a fenced code block is part of the text, not a requirement boundary.
    """
    outside = _outside_code_blocks(spec)
    section = _REQUIREMENTS_SECTION.search(outside)
    if section is None:
        return {}
    lines = spec.split("\n")
    texts: dict[str, str] = {}
    for block in _REQUIREMENT_BLOCK.finditer(outside, section.start(1), section.end(1)):
        first = outside.count("\n", 0, block.start(2)) + 1
        last = outside.count("\n", 0, block.end(2))
        if outside[block.end(2) - 1 : block.end(2)] != "\n":
            last += 1
        texts[block.group(1)] = "\n".join(line.rstrip() for line in lines[first:last]).strip()
    return texts


def changed_requirements(
    before: str | None, after: str | None
) -> list[tuple[str, RequirementChange]]:
    """Return the requirements *after* adds to or changes from *before*, in *after*'s order.

    ``None`` is a spec that does not exist.  Removed requirements are not returned, and a
    renamed one is added under its new name.
    """
    old = requirement_texts(before or "")
    changes: list[tuple[str, RequirementChange]] = []
    for name, text in requirement_texts(after or "").items():
        if name not in old:
            changes.append((name, "added"))
        elif old[name] != text:
            changes.append((name, "changed"))
    return changes


async def _spec_at(repo: str, path: str, ref: str) -> str | None:
    try:
        return (await github.get_raw_file(repo, path, ref=ref)).decode("utf-8")
    except FileNotFoundError:
        return None


async def spec_change_requirements(
    repo: str, number: int
) -> list[tuple[str, str, RequirementChange]] | None:
    """Return the requirements spec change *number* adds or changes, or ``None`` if it is gone.

    Each is ``(capability, requirement, change)``, read by comparing every capability's
    spec at the pull request's merge base and head.  A failed GitHub read raises.
    """
    commits = await github.get_merge_base(repo, number)
    if commits is None:
        return None
    base, head = commits
    capabilities = sorted(
        match.group(1)
        for path in set(await github.list_pull_request_files(repo, number))
        if (match := _SPEC_PATH.fullmatch(path))
    )
    changes: list[tuple[str, str, RequirementChange]] = []
    for capability in capabilities:
        path = _spec_path(capability)
        before, after = await asyncio.gather(_spec_at(repo, path, base), _spec_at(repo, path, head))
        changes.extend(
            (capability, name, change) for name, change in changed_requirements(before, after)
        )
    return changes


def _check_capabilities(
    capabilities: Mapping[str, str | None], refusal: str = "Spec change refused"
) -> None:
    """Refuse with *refusal*, listing every malformed capability name and content at once."""
    problems: list[str] = []
    for name, spec in capabilities.items():
        if not _CAPABILITY_NAME.fullmatch(name):
            problems.append(f"Invalid capability name {name!r}")
        elif spec is not None:
            problems.extend(spec_problems(name, spec))
    if problems:
        raise ValueError(
            f"{refusal}; fix every problem and retry:\n"
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
    repo: str,
    title: str,
    why: str,
    capabilities: dict[str, str | None],
    author_request: bool = False,
) -> dict[str, Any]:
    """Open a pull request labelled ``spec-change`` that edits one or more capabilities.

    *author_request* marks a change the author asked for in conversation: its pull
    request is also labelled ``author-request``, which ranks its work high in the backlog.

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
    labels = [SPEC_CHANGE_LABEL, AUTHOR_REQUEST_LABEL] if author_request else [SPEC_CHANGE_LABEL]
    try:
        await github.add_labels(repo, pr["number"], labels)
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Opened spec change #{pr['number']} ({pr['url']}) but could not label it "
            f"{', '.join(labels)} ({exc!r}). Add the labels by hand: until it has "
            f"{SPEC_CHANGE_LABEL!r}, the pull request is not listed against the capabilities "
            "it touches."
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
        if await github.update_issue(repo, pr_number, {"body": why}) is None:
            raise FileNotFoundError(f"Pull request #{pr_number} not found in {repo!r}")
    return {"repo": repo, "number": pr_number, "url": pull["url"]}


class SpecProgress(NamedTuple):
    """A spec repository's spec changes, each derived, and its open spec gaps by capability.

    ``landing_candidates`` pairs every spec change's entry with its pull request's
    description, which the entry itself does not carry.  ``references`` holds the
    reference block of every issue naming a spec change, by issue number, and
    ``facts`` the facts read for those issues, partial or empty when the read failed.
    """

    spec_gaps: dict[str, int]
    landing_candidates: list[landing.SpecChangeCandidate]
    references: dict[int, SpecReference]
    facts: dict[int, IssueFacts]


def _spec_change_entry(
    pull: github.IssueSummary,
    numbers: list[int],
    facts: dict[int, IssueFacts],
    failure: ActivityFailure | None,
) -> dict[str, Any]:
    state: SpecChangeState = "merged" if pull["merged"] else pull["state"]
    pr = (
        {
            "number": pull["number"],
            "state": "open",
            "merged": False,
            "url": pull["url"],
            "created_at": pull["created_at"],
        }
        if state == "open"
        else None
    )
    reason = spec_change_dropped(state, numbers, facts)
    if pr is not None:
        warning: str | None = f"awaiting merge of PR #{pull['number']}"
    else:
        warning = None if numbers or reason else "no issues name this spec change"
    entry: dict[str, Any] = {
        "spec_change": pull["number"],
        "title": pull["title"],
        "url": pull["url"],
        "state": state,
        "pr": pr,
        "warning": warning,
        "dropped": reason is not None,
        "derived": derive_spec_change(state, pull["updated_at"], numbers, facts, failure=failure),
    }
    if reason is not None:
        entry["reason"] = reason
    return entry


SPEC_CHANGE_ONLY_STATUSES: frozenset[str] = frozenset({"none", "unreadable"})


def spec_change_status(change: dict[str, Any]) -> str:
    """The status a spec change is counted under: its derived status, or why it has none.

    ``none`` is a spec change no issue names; ``unreadable`` one whose issues could
    not be read.
    """
    derived = change["derived"]
    if derived["status"] is not None:
        return str(derived["status"])
    return "unreadable" if derived["source"] == "unavailable" else "none"


async def read_spec_progress(repo: str) -> SpecProgress:
    """Derive every spec change in *repo* and count its open spec gaps by capability.

    One listing of the repository's issues and pull requests supplies the pull
    requests labelled ``spec-change``, the issues whose reference block names
    each one, and the open issues labelled ``spec-gap``; the named issues' facts
    then come in one batch.  A spec-gap issue without a reference block names no
    capability and is not counted.  Nothing is written.
    """
    items = await github.list_all_issues(repo)
    naming: dict[int, list[int]] = {}
    references: dict[int, SpecReference] = {}
    spec_gaps: dict[str, int] = {}
    for item in items:
        if item["is_pull_request"]:
            continue
        ref = parse_reference(item["body"])
        if ref is None:
            continue
        if ref.spec_change is not None:
            references[item["number"]] = ref
            naming.setdefault(ref.spec_change, []).append(item["number"])
        elif item["state"] == "open" and SPEC_GAP_LABEL in item["labels"]:
            spec_gaps[ref.capability] = spec_gaps.get(ref.capability, 0) + 1

    pulls = sorted(
        (item for item in items if item["is_pull_request"] and SPEC_CHANGE_LABEL in item["labels"]),
        key=lambda item: item["number"],
    )
    numbers = sorted({n for pull in pulls for n in naming.get(pull["number"], [])})
    facts: dict[int, IssueFacts] = {}
    failure: ActivityFailure | None = None
    try:
        activity = await github.get_issue_activity(repo, numbers)
        facts, failure = activity["facts"], activity["failure"]
    except Exception as exc:
        logger.warning(
            "failed to fetch issue activity for repo=%s error=%r; spec changes not derived",
            repo,
            exc,
        )
        failure = "transient"
    return SpecProgress(
        dict(sorted(spec_gaps.items())),
        [
            landing.SpecChangeCandidate(
                pull["body"],
                _spec_change_entry(pull, sorted(naming.get(pull["number"], [])), facts, failure),
            )
            for pull in pulls
        ],
        references,
        facts,
    )


async def list_spec_changes(repo: str, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Return every spec change in *repo* with its ``derived`` block, oldest first.

    Each entry carries ``spec_change`` (its pull request number), ``title``, ``url``,
    ``state`` (``open``, ``merged`` or ``closed``), ``pr`` (the open pull request the
    change awaits, ``None`` once it has merged or closed), ``warning``, ``dropped``
    and, when dropped, its ``reason``.  Its issues are those whose reference block
    names it.  Dropped spec changes are excluded unless ``include_inactive`` is true.
    """
    repo = await _resolve_repo(repo)
    if not await uses_specs(repo):
        raise ValueError(
            _not_using_specs(repo)["message"]
            + " Its work is tracked in requirement files: use list_requirements."
        )
    progress = await read_spec_progress(repo)
    await landing.notify_landed(repo, progress.landing_candidates)
    return [
        candidate.entry
        for candidate in progress.landing_candidates
        if include_inactive or not candidate.entry["dropped"]
    ]
