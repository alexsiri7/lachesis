"""Stub implementations for all 7 Lachesis MCP tools.

Real implementations are delivered in issues #4-#10.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, cast

import frontmatter

import github


async def list_requirements(repo: str) -> list[dict[str, Any]]:
    """Return all requirements for a given repo (id, title, status, github_issue)."""
    files = await github.list_files(repo, "requirements")
    results: list[dict[str, Any]] = []
    for f in files:
        if not f["name"].endswith(".md"):
            continue
        content, _ = await github.get_file(repo, f["path"])
        post = frontmatter.loads(content)
        results.append(
            {
                "id": post["id"],
                "title": post["title"],
                "status": post["status"],
                "github_issue": post.get("github_issue"),
            }
        )
    return results


async def get_requirement(repo: str, id: str) -> str:
    """Return the full markdown content of a single requirement file."""
    files = await github.list_files(repo, "requirements")
    matching = [f for f in files if f["name"].startswith(f"{id}-")]
    if not matching:
        raise FileNotFoundError(f"Requirement {id!r} not found in {repo!r}")
    content, _ = await github.get_file(repo, matching[0]["path"])
    return content


async def create_requirement(repo: str, title: str, why: str, what: str) -> dict[str, Any]:
    """Create a new requirement file and return its path and auto-assigned id."""
    # 1. List existing files to determine next ID and detect slug collisions
    files = await github.list_files(repo, "requirements")

    # 2. Assign next ID
    ids = []
    for f in files:
        name = f["name"]
        prefix = name.split("-")[0]
        if prefix.isdigit():
            ids.append(int(prefix))
    next_id = max(ids, default=0) + 1
    id_str = f"{next_id:03d}"

    # 3. Build slug from title
    slug = re.sub(r"[^a-z0-9-]+", "", title.lower().replace(" ", "-"))
    slug = slug.strip("-")

    # 4. Handle slug collisions
    existing_names = {f["name"] for f in files}
    candidate = f"{id_str}-{slug}.md"
    suffix = 2
    while candidate in existing_names:
        candidate = f"{id_str}-{slug}-{suffix}.md"
        suffix += 1

    path = f"requirements/{candidate}"
    today = date.today().isoformat()

    # 5. Build frontmatter + body
    post = frontmatter.Post(
        f"## Why\n\n{why}\n\n## What\n\n{what}\n\n## Issues\n\n_None yet._\n",
        id=id_str,
        title=title,
        status="idea",
        github_issue=None,
        created=today,
        updated=today,
    )
    content = frontmatter.dumps(post)

    # 6. Create file in GitHub
    await github.create_file(repo, path, content, f"feat: add requirement {id_str}")
    return {"id": id_str, "path": path}


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
    return cast(str, updated_content)


async def create_github_issues(repo: str, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create one or more GitHub issues and return their numbers/titles/urls."""
    results = []
    for issue in issues:
        result = await github.create_issue(
            repo,
            issue["title"],
            issue["body"],
            issue.get("labels"),
        )
        results.append(result)
    return results
