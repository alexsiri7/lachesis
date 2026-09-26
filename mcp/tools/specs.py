"""MCP tool implementations for reading a repository's OpenSpec specs."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import github

from .requirements import _resolve_repo

SPECS_DIR = "openspec/specs"
SPEC_CHANGE_LABEL = "spec-change"

_CAPABILITY_NAME = re.compile(r"\w[\w.-]*")
_PURPOSE = re.compile(r"^## Purpose[ \t]*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
_REQUIREMENT = re.compile(r"^### Requirement:", re.MULTILINE)


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
