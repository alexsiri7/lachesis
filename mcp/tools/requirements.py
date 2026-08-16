"""MCP tool implementations for Lachesis requirements management."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any, cast

import frontmatter
import httpx

import db
import github
from auth import current_db_user_id

from .repos import normalize_repo

logger = logging.getLogger(__name__)

_VALID_STATUSES = {"draft", "idea", "planned", "in-progress", "done"}

_INSTRUCTIONS = """\
Lachesis workflow — follow these steps in order:

1. create_requirement — capture intent (title, why, what). Requirement starts as 'draft'.
2. create_github_issues — break it into implementation tasks. Pass issues with title and body.
3. link_issues — attach the issue numbers to the requirement. Status advances to 'idea'.

A requirement without linked issues is a draft. Archon will not act on drafts.
"""


async def get_initial_instructions() -> str:
    """Return the standard Lachesis workflow for new Claude sessions."""
    return _INSTRUCTIONS


async def _resolve_repo(repo: str) -> str:
    """Resolve a short name (e.g. 'lachesis') to a registered 'owner/repo' string.

    If the input already contains '/' (or is a full URL) it is normalised and
    returned as-is.  Otherwise the user's registered repos are searched for one
    whose final path component matches the short name.  Falls back to the raw
    input if no match is found or if there is no auth context.
    """
    try:
        normalized = normalize_repo(repo)
    except ValueError:
        normalized = repo

    if "/" in normalized:
        return normalized

    user_id = current_db_user_id.get()
    if user_id:
        rows = db.list_repos(user_id)
        for row in rows:
            try:
                full = normalize_repo(row["repo_name"])
            except ValueError:
                continue
            if full.split("/")[-1].lower() == normalized.lower():
                return full

    return normalized


async def _locate_requirement(repo: str, id: str) -> tuple[str, str, str, str | None]:
    """Locate a requirement file on the default branch or an open PR branch.

    Returns ``(path, content, sha, branch)``.  *branch* is ``None`` for the
    default branch.  Raises :class:`FileNotFoundError` if not found.
    """
    files = await github.list_files(repo, "requirements")
    matching = [f for f in files if f["name"].startswith(f"{id}-")]
    branch: str | None = None
    if not matching:
        branch = await github.find_pr_branch(repo, f"lachesis/req-{id}-")
        if branch is None:
            raise FileNotFoundError(f"Requirement {id!r} not found in {repo!r}")
        files = await github.list_files(repo, "requirements", branch=branch)
        matching = [f for f in files if f["name"].startswith(f"{id}-")]
        if not matching:
            raise FileNotFoundError(f"Requirement {id!r} not found in {repo!r}")
    path = matching[0]["path"]
    content, sha = await github.get_file(repo, path, branch=branch)
    return path, content, sha, branch


async def list_requirements(repo: str) -> list[dict[str, Any]]:
    """Return all requirements for a given repo (id, title, status, github_issue)."""
    repo = await _resolve_repo(repo)
    files = await github.list_files(repo, "requirements")
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for f in files:
        if not f["name"].endswith(".md"):
            continue
        content, _ = await github.get_file(repo, f["path"])
        post = frontmatter.loads(content)
        req_id = post["id"]
        seen_ids.add(req_id)
        github_issue = post.get("github_issue")
        results.append(
            {
                "id": req_id,
                "title": post["title"],
                "status": post["status"],
                "github_issue": github_issue,
                "warning": "no linked issues" if github_issue is None else None,
            }
        )

    lachesis_prs = await github.list_lachesis_prs(repo)
    for pr_info in lachesis_prs:
        branch = pr_info["branch"]
        pr_url = pr_info["pr_url"]
        pr_files = await github.list_files(repo, "requirements", branch=branch)
        for f in pr_files:
            if not f["name"].endswith(".md"):
                continue
            content, _ = await github.get_file(repo, f["path"], branch=branch)
            post = frontmatter.loads(content)
            req_id = post["id"]
            if req_id in seen_ids:
                continue
            seen_ids.add(req_id)
            github_issue = post.get("github_issue")
            results.append(
                {
                    "id": req_id,
                    "title": post["title"],
                    "status": post["status"],
                    "github_issue": github_issue,
                    "pr_url": pr_url,
                    "warning": "no linked issues" if github_issue is None else None,
                }
            )

    return results


async def get_requirement(repo: str, id: str) -> str:
    """Return the full markdown content of a single requirement file."""
    repo = await _resolve_repo(repo)
    _, content, _, _ = await _locate_requirement(repo, id)
    return content


async def create_requirement(repo: str, title: str, why: str, what: str) -> dict[str, Any]:
    """Create a new requirement file and return its id and path.

    Returns ``{"id": str, "path": str}``.  When the target repo has branch
    protection on ``main``, the file is committed to a new branch and a pull
    request is opened automatically; in that case the dict also contains
    ``"pr_url": str`` with the URL of the created PR.
    """
    repo = await _resolve_repo(repo)
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
        status="draft",
        github_issue=None,
        created=today,
        updated=today,
    )
    content = frontmatter.dumps(post)

    # 6. Create file in GitHub — with branch protection fallback
    branch_name = f"lachesis/req-{id_str}-{slug}"
    pr_url: str | None = None
    try:
        await github.create_file(repo, path, content, f"feat: add requirement {id_str}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise
        logger.warning(
            "create_requirement: 409 on main for repo=%s, falling back to branch+PR",
            repo,
        )
        sha = await github.get_branch_sha(repo, "main")
        await github.create_branch(repo, branch_name, sha)
        try:
            await github.create_file(
                repo, path, content, f"feat: add requirement {id_str}", branch=branch_name
            )
            pr = await github.create_pull_request(
                repo,
                title=f"[Lachesis] Add requirement {id_str}: {title}",
                body=(
                    f"This pull request was automatically created by Lachesis.\n\n"
                    f"**Requirement:** {id_str} — {title}\n"
                    f"**File:** `{path}`\n\n"
                    f"Review and merge to add this requirement to `main`."
                ),
                head=branch_name,
                base="main",
            )
        except Exception:
            logger.warning(
                "create_requirement: fallback failed mid-flight repo=%s branch=%s; "
                "branch may be orphaned",
                repo,
                branch_name,
            )
            raise
        pr_url = pr["url"]
        # Attempt to enable automerge — best-effort; PR creation already succeeded,
        # so any automerge failure is swallowed and the pr_url is returned for manual merge.
        try:
            repo_settings = await github.get_repo_settings(repo)
            if repo_settings["allow_auto_merge"]:
                if repo_settings["allow_squash_merge"]:
                    merge_method = "SQUASH"
                elif repo_settings["allow_merge_commit"]:
                    merge_method = "MERGE"
                else:
                    merge_method = "REBASE"
                await github.enable_automerge(pr["node_id"], merge_method)
                if not repo_settings["delete_branch_on_merge"]:
                    logger.info(
                        "create_requirement: automerge enabled but delete_branch_on_merge "
                        "is disabled on repo=%s — branch will not auto-delete",
                        repo,
                    )
            else:
                logger.info(
                    "create_requirement: automerge not available on repo=%s "
                    "(allow_auto_merge=False) — user must merge manually",
                    repo,
                )
        except Exception as exc:
            logger.warning(
                "create_requirement: enable_automerge failed repo=%s pr=%s error=%r; "
                "pr_url returned for manual merge",
                repo,
                pr["number"],
                exc,
            )
    result: dict[str, Any] = {"id": id_str, "path": path}
    if pr_url is not None:
        result["pr_url"] = pr_url
    return result


async def update_requirement(repo: str, id: str, fields: dict[str, Any]) -> str:
    """Update fields (title, why, what, status) in an existing requirement."""
    repo = await _resolve_repo(repo)
    path, content, sha, branch = await _locate_requirement(repo, id)
    post = frontmatter.loads(content)

    allowed = {"title", "why", "what", "status"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown fields: {unknown!r}. Allowed: {allowed!r}")

    if "status" in fields and fields["status"] not in _VALID_STATUSES:
        raise ValueError(f"Invalid status {fields['status']!r}. Must be one of {_VALID_STATUSES}")

    for key in ("title", "status"):
        if key in fields:
            post[key] = fields[key]

    body = post.content
    for section, key in (("Why", "why"), ("What", "what")):
        if key not in fields:
            continue
        new_block = f"## {section}\n\n{fields[key]}\n"
        body, n = re.subn(
            rf"^## {section}\b[^\n]*\n(?:(?!^## )[^\n]*\n?)*",
            new_block,
            body,
            flags=re.MULTILINE,
        )
        if n == 0:
            body = body.rstrip("\n") + f"\n\n{new_block}"
    post.content = body
    post["updated"] = date.today().isoformat()

    updated_content = frontmatter.dumps(post)
    await github.update_file(
        repo, path, updated_content, sha, f"chore: update requirement {id}", branch=branch
    )
    return cast(str, updated_content)


async def delete_requirement(repo: str, id: str) -> dict[str, Any]:
    """Delete a requirement. Refuses if status is 'done'."""
    repo = await _resolve_repo(repo)
    path, content, sha, branch = await _locate_requirement(repo, id)
    post = frontmatter.loads(content)
    if post.get("status") == "done":
        raise ValueError(
            "Cannot delete a done requirement — it represents implemented code. "
            "Create a new requirement to remove the feature."
        )

    await github.delete_file(repo, path, sha, f"chore: delete requirement {id}", branch=branch)
    return {"deleted": path}


async def link_issues(repo: str, id: str, issues: list[dict[str, Any]]) -> str:
    """Write GitHub issue numbers into the requirement's Issues section."""
    repo = await _resolve_repo(repo)
    path, content, sha, branch = await _locate_requirement(repo, id)
    post = frontmatter.loads(content)

    # Update metadata
    if issues:
        post["github_issue"] = issues[0]["number"]
    if post.get("status") == "draft":
        post["status"] = "idea"
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
        branch=branch,
    )
    return cast(str, updated_content)


async def create_github_issues(
    repo: str, issues: list[dict[str, Any]], project_id: str | None = None
) -> list[dict[str, Any]]:
    """Create one or more GitHub issues and return their numbers/titles/urls."""
    repo = await _resolve_repo(repo)
    # Phase 1: Create all issues, build number→id map
    results: list[dict[str, Any]] = []
    number_to_id: dict[int, int] = {}
    for issue in issues:
        result = await github.create_issue(
            repo,
            issue["title"],
            issue["body"],
            issue.get("labels"),
        )
        node_id = result.pop("node_id")
        results.append({**result, "blocked_by": [], "added_to_project": False})
        number_to_id[result["number"]] = result["id"]

        if project_id:
            try:
                await github.add_to_project(project_id, node_id)
                results[-1]["added_to_project"] = True
            except Exception:
                logger.warning(
                    "create_github_issues: failed to add issue %s to project %s",
                    result["number"],
                    project_id,
                )

    # Phase 2: Wire blocked_by dependencies
    for issue_input, result in zip(issues, results, strict=True):
        blocked_by: list[int] = issue_input.get("blocked_by") or []
        for blocker_number in blocked_by:
            if blocker_number in number_to_id:
                blocker_id = number_to_id[blocker_number]
            else:
                # Fetch from GitHub — pre-existing issue
                blocker = await github.get_issue(repo, blocker_number)
                if blocker is None:
                    raise ValueError(f"Blocker issue #{blocker_number} not found in {repo!r}")
                blocker_id = blocker["id"]
            await github.add_issue_dependency(repo, result["number"], blocker_id)
            result["blocked_by"].append(blocker_number)

    return results


async def get_dashboard(status: str | None = None) -> dict[str, Any]:
    """Return cross-repo requirements summary, optionally filtered by status."""
    if status is not None and status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}. Must be one of {_VALID_STATUSES}")

    user_id = current_db_user_id.get()
    if not user_id:
        raise RuntimeError(
            "No DB user ID in request context. "
            "This may indicate a database connectivity issue (check server logs) "
            "or a token that predates the lachesis_uid claim."
        )

    repos = db.list_repos(user_id)
    aggregated: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}

    for repo_row in repos:
        repo_name = normalize_repo(repo_row["repo_name"])
        try:
            files = await github.list_files(repo_name, "requirements")
            requirements: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for f in files:
                if not f["name"].endswith(".md"):
                    continue
                content, _ = await github.get_file(repo_name, f["path"])
                post = frontmatter.loads(content)
                req_id = post["id"]
                seen_ids.add(req_id)
                req_status = post.get("status", "")
                status_counts[req_status] = status_counts.get(req_status, 0) + 1
                if status is not None and req_status != status:
                    continue
                github_issue = post.get("github_issue")
                requirements.append(
                    {
                        "id": req_id,
                        "title": post["title"],
                        "status": req_status,
                        "github_issue": github_issue,
                        "warning": "no linked issues" if github_issue is None else None,
                    }
                )

            lachesis_prs = await github.list_lachesis_prs(repo_name)
            for pr_info in lachesis_prs:
                branch = pr_info["branch"]
                pr_url = pr_info["pr_url"]
                pr_files = await github.list_files(repo_name, "requirements", branch=branch)
                for f in pr_files:
                    if not f["name"].endswith(".md"):
                        continue
                    content, _ = await github.get_file(repo_name, f["path"], branch=branch)
                    post = frontmatter.loads(content)
                    req_id = post["id"]
                    if req_id in seen_ids:
                        continue
                    seen_ids.add(req_id)
                    req_status = post.get("status", "")
                    status_counts[req_status] = status_counts.get(req_status, 0) + 1
                    if status is not None and req_status != status:
                        continue
                    github_issue = post.get("github_issue")
                    requirements.append(
                        {
                            "id": req_id,
                            "title": post["title"],
                            "status": req_status,
                            "github_issue": github_issue,
                            "pr_url": pr_url,
                            "warning": "no linked issues" if github_issue is None else None,
                        }
                    )

            aggregated.append({"repo": repo_name, "requirements": requirements})
        except Exception:
            logger.warning("get_dashboard: failed to fetch requirements from %s", repo_name)

    total = sum(status_counts.values())
    unlinked_count = sum(
        1
        for repo_entry in aggregated
        for req in repo_entry["requirements"]
        if req["github_issue"] is None
    )
    return {
        "repos": aggregated,
        "summary": {"total": total, "by_status": status_counts, "unlinked_count": unlinked_count},
    }


async def get_github_issue(repo: str, issue_number: int) -> dict[str, Any]:
    """Return the current state of a GitHub issue.

    Returns title, body, state (open/closed), assignees, labels, and linked PR URL.
    """
    repo = await _resolve_repo(repo)
    issue = await github.get_issue(repo, issue_number)
    if issue is None:
        raise FileNotFoundError(f"Issue #{issue_number} not found in {repo!r}")
    return issue


async def update_github_issue(
    repo: str, issue_number: int, fields: dict[str, Any]
) -> dict[str, Any]:
    """Update title, body, and/or state of a GitHub issue.

    *fields* accepts any combination of:
    - ``title`` — new issue title
    - ``body`` — new issue body
    - ``state`` — ``"open"`` or ``"closed"``

    Returns ``number``, ``title``, and ``url`` of the updated issue.
    """
    if not fields:
        raise ValueError("fields must not be empty")
    unknown = set(fields) - {"title", "body", "state"}
    if unknown:
        raise ValueError(f"Unknown fields: {unknown!r}. Allowed: title, body, state")
    if "state" in fields and fields["state"] not in {"open", "closed"}:
        raise ValueError(f"Invalid state {fields['state']!r}. Must be 'open' or 'closed'")
    if "title" in fields and not str(fields["title"]).strip():
        raise ValueError("title must not be empty")
    if "body" in fields and fields["body"] is not None and not str(fields["body"]).strip():
        raise ValueError("body must not be empty")
    repo = await _resolve_repo(repo)
    result = await github.update_issue(repo, issue_number, fields)
    if result is None:
        raise FileNotFoundError(f"Issue #{issue_number} not found in {repo!r}")
    return result


async def get_dependencies(repo: str, issue_number: int) -> dict[str, Any]:
    """Return the blocked_by and blocking dependency lists for a GitHub issue.

    Returns: {"blocked_by": [int, ...], "blocking": [int, ...]}
    """
    repo = await _resolve_repo(repo)
    deps = await github.get_issue_dependencies(repo, issue_number)
    if deps is None:
        raise FileNotFoundError(f"Issue #{issue_number} not found in {repo!r}")
    return deps


async def add_dependency(
    repo: str, issue_number: int, blocked_by_issue_number: int
) -> dict[str, Any]:
    """Mark issue_number as blocked by blocked_by_issue_number.

    Returns the updated dependency state: {"blocked_by": [int, ...], "blocking": [int, ...]}
    """
    repo = await _resolve_repo(repo)
    # Validate both issues exist before mutating (parallelize to save one round trip)
    issue, blocked_by_issue = await asyncio.gather(
        github.get_issue(repo, issue_number),
        github.get_issue(repo, blocked_by_issue_number),
    )
    if issue is None:
        raise FileNotFoundError(f"Issue #{issue_number} not found in {repo!r}")
    if blocked_by_issue is None:
        raise FileNotFoundError(f"Issue #{blocked_by_issue_number} not found in {repo!r}")
    await github.add_issue_dependency(repo, issue_number, blocked_by_issue["id"])
    deps = await github.get_issue_dependencies(repo, issue_number)
    if deps is None:
        raise FileNotFoundError(f"Issue #{issue_number} not found in {repo!r}")
    return deps
