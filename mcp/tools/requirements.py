"""Stub implementations for all 7 Lachesis MCP tools.

Real implementations are delivered in issues #4-#10.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import frontmatter

import github


async def list_requirements(repo: str) -> list[dict[str, Any]]:
    """Return all requirements for a given repo (id, title, status, github_issue)."""
    raise NotImplementedError("Implemented in issue #4")


async def get_requirement(repo: str, id: str) -> str:
    """Return the full markdown content of a single requirement file."""
    raise NotImplementedError("Implemented in issue #5")


async def create_requirement(repo: str, title: str, why: str, what: str) -> dict[str, Any]:
    """Create a new requirement file and return its path and auto-assigned id."""
    raise NotImplementedError("Implemented in issue #6")


async def update_requirement(repo: str, id: str, fields: dict[str, Any]) -> str:
    """Update fields (title, why, what, status) in an existing requirement."""
    raise NotImplementedError("Implemented in issue #7")


async def delete_requirement(repo: str, id: str) -> dict[str, Any]:
    """Delete a requirement. Refuses if status is 'done'."""
    # Locate the requirement file
    files = await github.list_files(repo, "requirements")
    matching = [f for f in files if f["name"].startswith(f"{id}-")]
    if not matching:
        raise FileNotFoundError(f"Requirement {id!r} not found in {repo!r}")

    path = matching[0]["path"]
    content, sha = await github.get_file(repo, path)

    # Parse frontmatter to check status
    post = frontmatter.loads(content)
    if post.get("status") == "done":
        raise ValueError(
            "Cannot delete a done requirement — it represents implemented code. "
            "Create a new requirement to remove the feature."
        )

    # Delete the file
    await github.delete_file(repo, path, sha, f"chore: delete requirement {id}")
    return {"deleted": path}


async def link_issues(repo: str, id: str, issues: list[dict[str, Any]]) -> str:
    """Write GitHub issue numbers into the requirement's Issues section."""
    # Locate the requirement file
    files = await github.list_files(repo, "requirements")
    matching = [f for f in files if f["name"].startswith(f"{id}-")]
    if not matching:
        raise FileNotFoundError(f"Requirement {id!r} not found in {repo!r}")

    path = matching[0]["path"]
    content, sha = await github.get_file(repo, path)

    # Parse frontmatter
    post = frontmatter.loads(content)

    # Update metadata
    if issues:
        post["github_issue"] = issues[0]["number"]
    post["updated"] = date.today().isoformat()

    # Build new ## Issues section body
    bullet_block = "\n".join(f"- #{i['number']} \u2014 {i['title']}" for i in issues)
    new_section = f"## Issues\n\n{bullet_block}\n" if bullet_block else "## Issues\n"

    # Replace existing ## Issues section in the body, or append if absent
    body = post.content
    body, n = re.subn(
        r"^## Issues\b[^\n]*\n(?:(?!^## )[^\n]*\n?)*",
        new_section,
        body,
        flags=re.MULTILINE,
    )
    if n == 0:
        body = body.rstrip("\n") + "\n\n" + new_section
    post.content = body

    # Serialize and write back
    updated_content = frontmatter.dumps(post)
    await github.update_file(
        repo,
        path,
        updated_content,
        sha,
        f"chore: link issues to requirement {id}",
    )
    return updated_content


async def create_github_issues(repo: str, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create one or more GitHub issues and return their numbers/titles/urls."""
    raise NotImplementedError("Implemented in issue #10")
