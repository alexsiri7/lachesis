"""MCP tool implementation for creating a new project: repository, specs and registration."""

from __future__ import annotations

from typing import Any

import github

from .repos import register_repo
from .specs import _check_capabilities, _write_capability


def _purpose_only_spec(name: str, purpose: str) -> str:
    return f"# {name}\n\n## Purpose\n\n{purpose.strip()}\n\n## Requirements\n"


async def create_project(
    name: str,
    purpose: str,
    capabilities: dict[str, str] | None = None,
    public: bool = False,
) -> dict[str, Any]:
    """Create repository *name*, seed its ``openspec/specs`` and register it, all or nothing.

    *capabilities* maps each initial capability name to its complete ``spec.md``
    content; without any, one capability named after the repository is seeded
    with *purpose* as its Purpose.  Every capability is format-checked and the
    name's availability is settled before anything is created.  A failure after
    the repository exists deletes it again.

    Returns ``{"repo", "url", "private", "capabilities"}``.
    """
    if not purpose.strip():
        raise ValueError("A project needs a purpose")
    specs = capabilities or {name: _purpose_only_spec(name, purpose)}
    _check_capabilities(specs, "Project creation refused")
    try:
        created = await github.create_repo(name, description=purpose.strip(), private=not public)
    except FileExistsError:
        raise ValueError(
            f"Project creation refused: the name {name!r} is already taken in your "
            "GitHub account. Nothing was created."
        ) from None
    repo = created["full_name"]
    try:
        for capability, spec in specs.items():
            await _write_capability(
                repo, created["default_branch"], capability, spec, f"spec: seed {capability}"
            )
        await register_repo(repo)
    except Exception as exc:
        try:
            await github.delete_repo(repo)
        except Exception as cleanup_exc:
            raise RuntimeError(
                f"Could not create project {repo!r} ({exc!r}), and could not delete the "
                f"half-created repository ({cleanup_exc!r}). Delete {created['html_url']} "
                "by hand; it is not registered with Lachesis."
            ) from exc
        raise RuntimeError(
            f"Could not create project {repo!r} ({exc!r}). The repository was deleted "
            "again and nothing was registered."
        ) from exc
    return {
        "repo": repo,
        "url": created["html_url"],
        "private": created["private"],
        "capabilities": sorted(specs),
    }
