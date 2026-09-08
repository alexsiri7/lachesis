"""MCP tool implementations for Lachesis requirements management."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, date, datetime
from typing import Any, NamedTuple, cast

import frontmatter
import httpx

import db
import github
import groups
import landing
from auth import current_db_user_id
from derive import IssueFacts, derive, linked_issue_numbers
from status import INACTIVE_STATUSES, VALID_STATUSES, canonical_status

from .repos import _require_user_id, normalize_repo

logger = logging.getLogger(__name__)

_INSTRUCTIONS = """\
Lachesis workflow — follow these steps in order:

1. create_requirement — capture intent (title, why, what). Requirement starts as 'draft'.
2. create_github_issues — break it into implementation tasks. Pass issues with title and body.
3. link_issues — attach the issue numbers to the requirement. Status advances to 'idea'.

A requirement without linked issues is a draft. Archon will not act on drafts.

New issues are ready and last in the backlog, eligible for autonomous pickup as
soon as they are created. Call set_backlog_flags with ready=False to park an issue
that is knowingly unfinished, and ready=True to unpark it. Use move_issue to order
the backlog by relative placement, and the interrupt flag for the one thing that
must jump the queue. Call next_issue to ask what to pick up — it returns the top
ready, unblocked issue, or explains why there is none.
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


async def _locate_requirement(repo: str, id: str) -> tuple[str, str, str, dict[str, Any] | None]:
    """Locate a requirement file on the default branch or an open PR branch.

    Returns ``(path, content, sha, pr)``.  *pr* is ``None`` for the default branch,
    otherwise the open pull request the file was read from.  Raises
    :class:`FileNotFoundError` if not found.
    """
    files = await github.list_files(repo, "requirements")
    matching = [f for f in files if f["name"].startswith(f"{id}-")]
    pr: dict[str, Any] | None = None
    branch: str | None = None
    if not matching:
        pr = await github.find_open_pr(repo, f"lachesis/req-{id}-")
        if pr is None:
            raise FileNotFoundError(f"Requirement {id!r} not found in {repo!r}")
        branch = pr["branch"]
        files = await github.list_files(repo, "requirements", branch=branch)
        matching = [f for f in files if f["name"].startswith(f"{id}-")]
        if not matching:
            raise FileNotFoundError(f"Requirement {id!r} not found in {repo!r}")
    path = matching[0]["path"]
    content, sha = await github.get_file(repo, path, branch=branch)
    return path, content, sha, pr


def _inactive_fields(post: frontmatter.Post) -> dict[str, Any]:
    """Return the cancelled/superseded companion fields the requirement actually carries.

    An included inactive requirement has to explain itself; an active one keeps
    exactly the key set it has always returned.
    """
    return {key: post[key] for key in ("reason", "superseded_by") if post.get(key)}


def _pr_field(pr_info: dict[str, Any] | None) -> dict[str, Any] | None:
    """Render the pull request a requirement was read from, or ``None`` for the default branch.

    ``state`` and ``merged`` are constants because every PR that reaches here came from a
    ``state=open`` query: once a requirement PR merges, its file is on the default branch,
    where it is found by the default-branch pass and reports no PR at all; a closed
    unmerged one drops out of the open list along with its requirement.
    """
    if pr_info is None:
        return None
    return {
        "number": pr_info["pr_number"],
        "state": "open",
        "merged": False,
        "url": pr_info["pr_url"],
        "branch": pr_info["branch"],
    }


def _entry_warning(pr: dict[str, Any] | None, numbers: list[int]) -> str | None:
    """An unmerged PR is reported even when issues are linked — merging it unblocks them."""
    if pr is not None:
        return f"awaiting merge of PR #{pr['number']}"
    return None if numbers else "no linked issues"


_Pending = list[tuple[dict[str, Any], list[int], bool]]


async def _attach_derived(repo: str, pending: _Pending) -> None:
    """Derive a status for each pending entry from one batch of issue activity.

    A repo whose requirements link no issues costs no request.  GitHub failing
    here must not cost the caller the listing it actually asked for, so the
    entries report an unavailable derivation instead of raising.
    """
    numbers = sorted({number for _, entry_numbers, _ in pending for number in entry_numbers})
    facts: dict[int, IssueFacts] | None = {}
    if numbers:
        try:
            facts = await github.get_issue_activity(repo, numbers)
        except Exception as exc:
            logger.warning(
                "failed to fetch issue activity for repo=%s error=%r; status not derived",
                repo,
                exc,
            )
            facts = None
    for entry, entry_numbers, on_pr_branch in pending:
        entry["derived"] = derive(entry["status"], entry_numbers, facts, on_pr_branch=on_pr_branch)


def _normalize_stored_status(post: frontmatter.Post) -> None:
    """Rewrite a variant spelling to its canonical form when the file is rewritten anyway.

    An unrecognised value is left byte-identical — only a known state is folded.
    """
    canonical = canonical_status(post.get("status"))
    if canonical in VALID_STATUSES and canonical != post.get("status"):
        post["status"] = canonical


async def list_requirements(repo: str, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Return all requirements for a given repo (id, title, status, github_issue).

    Cancelled and superseded requirements are excluded unless ``include_inactive``
    is true; when included they carry their ``reason`` / ``superseded_by`` field.

    Each entry also carries a ``derived`` block: the status its linked issues and
    their pull requests actually show, whether that diverges from the recorded
    one, when those issues last moved, and the individual silent-failure signals.
    """
    repo = await _resolve_repo(repo)
    files = await github.list_files(repo, "requirements")
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    pending: _Pending = []
    landing_candidates: list[landing.Candidate] = []

    for f in files:
        if not f["name"].endswith(".md"):
            continue
        content, _ = await github.get_file(repo, f["path"])
        post = frontmatter.loads(content)
        req_id = post["id"]
        seen_ids.add(req_id)
        req_status = canonical_status(post.get("status", ""))
        if not include_inactive and req_status in INACTIVE_STATUSES:
            continue
        numbers = linked_issue_numbers(post)
        entry = {
            "id": req_id,
            "title": post["title"],
            "status": req_status,
            "github_issue": post.get("github_issue"),
            "pr": None,
            "warning": _entry_warning(None, numbers),
            **_inactive_fields(post),
        }
        results.append(entry)
        pending.append((entry, numbers, False))
        landing_candidates.append(landing.Candidate(f["path"], content, entry))

    lachesis_prs = await github.list_lachesis_prs(repo)
    for pr_info in lachesis_prs:
        branch = pr_info["branch"]
        pr = _pr_field(pr_info)
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
            req_status = canonical_status(post.get("status", ""))
            if not include_inactive and req_status in INACTIVE_STATUSES:
                continue
            numbers = linked_issue_numbers(post)
            entry = {
                "id": req_id,
                "title": post["title"],
                "status": req_status,
                "github_issue": post.get("github_issue"),
                "pr": pr,
                "warning": _entry_warning(pr, numbers),
                **_inactive_fields(post),
            }
            results.append(entry)
            pending.append((entry, numbers, True))

    await _attach_derived(repo, pending)
    await landing.notify_landed(repo, landing_candidates)
    return results


async def get_requirement(repo: str, id: str) -> dict[str, Any]:
    """Return ``{"content", "pr"}`` for a single requirement file.

    ``content`` is the full markdown including frontmatter.  ``pr`` describes the open
    pull request the file was read from — a requirement whose file has not landed yet —
    and is ``None`` when the file was read from the default branch.
    """
    repo = await _resolve_repo(repo)
    _, content, _, pr_info = await _locate_requirement(repo, id)
    return {"content": content, "pr": _pr_field(pr_info)}


async def create_requirement(repo: str, title: str, why: str, what: str) -> dict[str, Any]:
    """Create a new requirement file and return its id and path.

    Returns ``{"id": str, "path": str}``.  When the target repo has branch
    protection on ``main``, the file is committed to a new branch and a pull
    request is opened automatically; in that case the dict also contains
    ``"pr_url": str`` with the URL of the created PR.
    """
    repo = await _resolve_repo(repo)
    # 1. Fetch main-branch requirements (used for ID assignment and slug collision detection)
    files = await github.list_files(repo, "requirements")

    # 2. Assign next ID — also scan open lachesis/* PR branches to avoid collisions
    all_files = list(files)
    try:
        lachesis_prs = await github.list_lachesis_prs(repo)
        for pr_info in lachesis_prs:
            try:
                pr_files = await github.list_files(repo, "requirements", branch=pr_info["branch"])
                all_files.extend(pr_files)
            except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
                logger.warning(
                    "create_requirement: failed to list files on PR branch=%s repo=%s"
                    " error=%r; skipping",
                    pr_info["branch"],
                    repo,
                    exc,
                )
    except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
        logger.warning(
            "create_requirement: PR-branch scan failed repo=%s error=%r; "
            "proceeding with main-branch IDs only (ID may be re-assigned if a PR exists)",
            repo,
            exc,
        )

    ids = []
    for f in all_files:
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
    existing_names = {f["name"] for f in all_files}
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


def _invalid_status_message(value: object) -> str:
    """Explain a rejected status, naming the canonical spelling when there is one."""
    hint = canonical_status(value)
    correction = f" — use {hint!r}" if hint in VALID_STATUSES else ""
    return f"Invalid status {value!r}{correction}. Must be one of {sorted(VALID_STATUSES)}"


def _validate_lifecycle_fields(
    fields: dict[str, Any], post: frontmatter.Post, resulting_status: str
) -> None:
    """Enforce that cancelled carries a reason, superseded a target, and neither leaks."""
    for field, owning_status in (("reason", "cancelled"), ("superseded_by", "superseded")):
        if field in fields and resulting_status != owning_status:
            raise ValueError(
                f"{field!r} is only valid with status {owning_status!r}, not {resulting_status!r}"
            )
        if resulting_status == owning_status and not (fields.get(field) or post.get(field)):
            raise ValueError(f"status {owning_status!r} requires {field!r}")


async def _normalize_superseded_by(value: object, repo: str) -> dict[str, str]:
    """Validate the replacement reference and fill in the repo it defaults to."""
    if not isinstance(value, dict) or set(value) - {"id", "repo"}:
        raise ValueError(
            f"'superseded_by' must be a mapping with keys 'id' and optional 'repo', got {value!r}"
        )
    raw_id = value.get("id")
    if isinstance(raw_id, int) and not isinstance(raw_id, bool):
        req_id = f"{raw_id:03d}"
    elif isinstance(raw_id, str) and raw_id.strip():
        req_id = raw_id.strip()
    else:
        raise ValueError(f"'superseded_by' needs a non-empty 'id', got {raw_id!r}")
    target_repo = value.get("repo")
    resolved = await _resolve_repo(target_repo) if target_repo else repo
    return {"repo": resolved, "id": req_id}


async def update_requirement(repo: str, id: str, fields: dict[str, Any]) -> str:
    """Update fields (title, why, what, status, reason, superseded_by) in a requirement.

    Setting ``status`` to ``cancelled`` requires a ``reason``; setting it to
    ``superseded`` requires ``superseded_by`` as ``{"id": ..., "repo": ...}``,
    where ``repo`` defaults to the requirement's own repo.
    """
    repo = await _resolve_repo(repo)
    path, content, sha, pr = await _locate_requirement(repo, id)
    branch = pr["branch"] if pr else None
    post = frontmatter.loads(content)
    _normalize_stored_status(post)

    allowed = {"title", "why", "what", "status", "reason", "superseded_by"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown fields: {unknown!r}. Allowed: {allowed!r}")

    if "status" in fields and fields["status"] not in VALID_STATUSES:
        raise ValueError(_invalid_status_message(fields["status"]))

    resulting_status = fields.get("status", canonical_status(post.get("status", "")))
    _validate_lifecycle_fields(fields, post, resulting_status)
    if "superseded_by" in fields:
        fields = {
            **fields,
            "superseded_by": await _normalize_superseded_by(fields["superseded_by"], repo),
        }

    for key in ("title", "status", "reason", "superseded_by"):
        if key in fields:
            post[key] = fields[key]
    if resulting_status != "cancelled":
        post.metadata.pop("reason", None)
    if resulting_status != "superseded":
        post.metadata.pop("superseded_by", None)

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
    path, content, sha, pr = await _locate_requirement(repo, id)
    branch = pr["branch"] if pr else None
    post = frontmatter.loads(content)
    if canonical_status(post.get("status")) == "done":
        raise ValueError(
            "Cannot delete a done requirement — it represents implemented code. "
            "Create a new requirement to remove the feature."
        )

    await github.delete_file(repo, path, sha, f"chore: delete requirement {id}", branch=branch)
    return {"deleted": path}


async def link_issues(repo: str, id: str, issues: list[dict[str, Any]]) -> str:
    """Write GitHub issue numbers into the requirement's Issues section."""
    repo = await _resolve_repo(repo)
    path, content, sha, pr = await _locate_requirement(repo, id)
    branch = pr["branch"] if pr else None
    post = frontmatter.loads(content)
    _normalize_stored_status(post)

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


async def _apply_backlog_overrides(
    repo: str, issues: list[dict[str, Any]], results: list[dict[str, Any]]
) -> None:
    """Apply per-issue ``ready`` / ``before`` / ``after`` overrides to created issues.

    The defaults they override are ready, last in the backlog, so the only flag
    worth writing here is an explicit ``ready: false`` — the park.

    Best-effort throughout: the GitHub issues already exist and cannot be
    un-created, so every failure is logged and reported through the result dict.
    Anchors are resolved issue by issue, so one naming an issue created earlier in
    the same batch works.
    """
    try:
        user_id = _require_user_id()
    except RuntimeError:
        logger.warning("create_github_issues: no DB user context; backlog overrides skipped")
        return

    for issue_input, result in zip(issues, results, strict=True):
        number = result["number"]
        requested = issue_input.get("ready")
        if requested is not None:
            try:
                db.upsert_backlog_flags(user_id, repo, number, ready=bool(requested))
                result["ready"] = bool(requested)
            except Exception:
                # Leaving the creation default in place makes a dropped park visible:
                # the reported flag disagrees with what the caller asked for.
                logger.warning(
                    "create_github_issues: failed to set ready=%s on issue %s", requested, number
                )

        before = issue_input.get("before")
        after = issue_input.get("after")
        if before is None and after is None:
            continue
        try:
            await move_issue(repo, number, before=before, after=after)
            result["placed"] = True
        except Exception:
            logger.warning(
                "create_github_issues: failed to place issue %s (before=%r, after=%r)",
                number,
                before,
                after,
            )


async def create_github_issues(
    repo: str, issues: list[dict[str, Any]], project_id: str | None = None
) -> list[dict[str, Any]]:
    """Create one or more GitHub issues and return their numbers/titles/urls.

    Each issue may carry ``ready`` (bool) and one of ``before`` / ``after`` (issue
    numbers) to override the defaults — ready, last in the backlog. Passing
    ``ready: false`` parks the issue, holding it back from autonomous pickup.
    """
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
        results.append(
            {
                **result,
                "blocked_by": [],
                "added_to_project": False,
                "ready": True,
                "placed": False,
            }
        )
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

    # Phase 3: Backlog overrides — the default path stays free of the DB and of auth
    if any(key in issue for issue in issues for key in ("ready", "before", "after")):
        await _apply_backlog_overrides(repo, issues, results)

    return results


# ---------------------------------------------------------------------------
# Backlog ordering
# ---------------------------------------------------------------------------


_PUBLIC_BACKLOG_KEYS = ("number", "title", "position", "ready", "interrupt")


def _hoist_interrupts(base: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Effective order: interrupt-marked issues first, each group keeping base order."""
    return [i for i in base if i["interrupt"]] + [i for i in base if not i["interrupt"]]


def _public_backlog(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project internal backlog entries onto the documented backlog response shape.

    `_base_backlog_order` also carries each issue's GitHub labels, which `next_issue`
    reads to skip work already in progress; they are not part of what the backlog
    tools promise their callers.
    """
    return [{key: item[key] for key in _PUBLIC_BACKLOG_KEYS} for item in items]


async def _base_backlog_order(repo: str, user_id: str) -> list[dict[str, Any]]:
    """Return the repo's open issues in base backlog order.

    Positioned rows come first in position order, then every remaining open issue
    by issue number ascending — which is creation order, so a new issue is last
    without anything being written. An issue with no row is ready: a row exists
    only to park, position or interrupt one. Closed issues drop out here; their
    rows stay in the table, so the ready and interrupt flags survive a close and
    reopen, though an explicit position does not — the next move clears it. Each entry's
    ``position`` is its index in this list, never in the interrupt-hoisted one.
    """
    open_issues = await github.list_open_issues(repo)
    titles = {issue["number"]: issue["title"] for issue in open_issues}
    labels = {issue["number"]: issue["labels"] for issue in open_issues}
    rows = {
        row["issue_number"]: row
        for row in db.list_backlog(user_id, repo)
        if row["issue_number"] in titles
    }

    positioned = sorted(
        (number for number, row in rows.items() if row["position"] is not None),
        key=lambda number: (rows[number]["position"], number),
    )
    remaining = sorted(number for number in titles if number not in set(positioned))

    return [
        {
            "number": number,
            "title": titles[number],
            "labels": labels[number],
            "position": index,
            "ready": bool(rows[number]["ready"]) if number in rows else True,
            "interrupt": bool(rows[number]["interrupt"]) if number in rows else False,
        }
        for index, number in enumerate(positioned + remaining)
    ]


async def get_backlog(repo: str) -> list[dict[str, Any]]:
    """Return the repo's open issues in backlog order, interrupts first.

    Each entry has ``number``, ``title``, ``position``, ``ready`` and ``interrupt``.
    ``position`` is the issue's place in the backlog itself, so an interrupt-marked
    issue leads the list while still reporting the position it will fall back to.
    """
    repo = await _resolve_repo(repo)
    user_id = _require_user_id()
    return _public_backlog(_hoist_interrupts(await _base_backlog_order(repo, user_id)))


async def move_issue(
    repo: str, issue_number: int, before: int | None = None, after: int | None = None
) -> list[dict[str, Any]]:
    """Move an issue immediately before or after another open issue.

    Exactly one of *before* / *after* is required — placement is always relative to
    a named issue, never an absolute rank. Returns the backlog as `get_backlog` does.
    """
    if (before is None) == (after is None):
        raise ValueError("exactly one of before/after is required")
    anchor = before if before is not None else after
    if anchor == issue_number:
        raise ValueError("cannot move an issue relative to itself")

    repo = await _resolve_repo(repo)
    user_id = _require_user_id()
    base = await _base_backlog_order(repo, user_id)
    numbers = [item["number"] for item in base]
    for number in (issue_number, anchor):
        if number not in numbers:
            raise FileNotFoundError(f"Issue #{number} is not an open issue in {repo!r}")

    numbers.remove(issue_number)
    anchor_index = numbers.index(cast(int, anchor))
    numbers.insert(anchor_index if before is not None else anchor_index + 1, issue_number)

    # The base order is what gets persisted — renumbering the interrupt-hoisted
    # order would bake an interrupt's temporary lead into its stored position.
    db.set_backlog_order(user_id, repo, numbers)

    by_number = {item["number"]: item for item in base}
    return _public_backlog(
        _hoist_interrupts(
            [{**by_number[number], "position": index} for index, number in enumerate(numbers)]
        )
    )


async def set_backlog_flags(
    repo: str, issue_number: int, ready: bool | None = None, interrupt: bool | None = None
) -> dict[str, Any]:
    """Set the ready and/or interrupt flags on an issue. An omitted flag is left alone.

    Returns ``{"number", "ready", "interrupt"}``.
    """
    if ready is None and interrupt is None:
        raise ValueError("at least one of ready/interrupt is required")
    repo = await _resolve_repo(repo)
    user_id = _require_user_id()
    if await github.get_issue(repo, issue_number) is None:
        raise FileNotFoundError(f"Issue #{issue_number} not found in {repo!r}")
    row = db.upsert_backlog_flags(user_id, repo, issue_number, ready=ready, interrupt=interrupt)
    return {
        "number": issue_number,
        "ready": bool(row["ready"]),
        "interrupt": bool(row["interrupt"]),
    }


_IN_PROGRESS_LABEL = "archon:in-progress"


class _RepoScan(NamedTuple):
    """What one repo's backlog walk found for `next_issue`."""

    candidate: dict[str, Any] | None
    blocked: list[dict[str, Any]]
    saw_open: bool
    saw_claimed: bool


async def _open_blockers(
    repo: str,
    issue_number: int,
    open_titles: dict[int, str],
    cache: dict[tuple[str, int], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """Return ``{"number", "title"}`` for each of *issue_number*'s blockers still open.

    A blocker in *open_titles* is known open for free. Anything else is confirmed by
    a direct fetch rather than assumed closed — `list_open_issues` returns only the
    first hundred issues, so absence from it is not evidence that a blocker is done,
    and returning a blocked issue is the one failure this must not make.
    """
    numbers = await github.list_blocked_by_numbers(repo, issue_number)
    blockers: list[dict[str, Any]] = []
    for number in numbers or []:
        if number in open_titles:
            blockers.append({"number": number, "title": open_titles[number]})
            continue
        if (repo, number) not in cache:
            cache[(repo, number)] = await github.get_issue(repo, number)
        blocker = cache[(repo, number)]
        if blocker is not None and blocker["state"] == "open":
            blockers.append({"number": number, "title": blocker["title"]})
    return blockers


async def _scan_repo(
    repo: str, user_id: str, cache: dict[tuple[str, int], dict[str, Any] | None]
) -> _RepoScan:
    """Walk one repo's backlog for the first pickable issue, reporting what it passed."""
    order = _hoist_interrupts(await _base_backlog_order(repo, user_id))
    open_titles = {item["number"]: item["title"] for item in order}
    candidate: dict[str, Any] | None = None
    blocked: list[dict[str, Any]] = []
    saw_claimed = False

    for item in order:
        # Interrupts lead the order, so keep walking that group even once a candidate
        # is held: a blocked interrupt has to be reported, never silently passed over.
        if candidate is not None and not item["interrupt"]:
            break
        if not item["ready"]:
            continue
        if _IN_PROGRESS_LABEL in item["labels"]:
            saw_claimed = True
            continue
        blockers = await _open_blockers(repo, item["number"], open_titles, cache)
        if blockers:
            blocked.append(
                {
                    "repo": repo,
                    "number": item["number"],
                    "title": item["title"],
                    "interrupt": item["interrupt"],
                    "blocked_by": blockers,
                }
            )
        elif candidate is None:
            candidate = {
                "repo": repo,
                "number": item["number"],
                "title": item["title"],
                "position": item["position"],
                "interrupt": item["interrupt"],
            }

    return _RepoScan(candidate, blocked, bool(order), saw_claimed)


def _describe_blocked(entries: list[dict[str, Any]]) -> str:
    """Render blocked entries as ``owner/repo#12 by #8, #9; owner/repo#14 by #3``."""
    return "; ".join(
        "{}#{} by {}".format(
            entry["repo"],
            entry["number"],
            ", ".join(f"#{blocker['number']}" for blocker in entry["blocked_by"]),
        )
        for entry in entries
    )


async def next_issue(repo: str | None = None) -> dict[str, Any]:
    """Return the issue the worker should pick up next, or explain why there is none.

    Considers only issues that are ready, free of open blockers, and not already
    carrying the ``archon:in-progress`` label; an interrupt-marked issue wins over
    position, including across repos. Omitting *repo* considers every registered
    repo, in which case runners-up are listed under ``also_eligible`` because
    backlog position only orders issues within one repo.

    Returns ``issue`` (``None`` when nothing qualifies), a ``reason`` of
    ``selected`` / ``all_blocked`` / ``all_in_progress`` / ``none_ready`` / ``empty``,
    a one-sentence ``explanation``, and ``blocked`` — the ready issues passed over
    because a dependency of theirs is still open, each naming its blockers. An unscoped
    call skips a repo it cannot read and says so in the explanation, rather than letting
    one unreachable repo cost the caller every other repo's backlog.
    """
    user_id = _require_user_id()
    if repo is not None:
        repos = [await _resolve_repo(repo)]
    else:
        repos = [normalize_repo(row["repo_name"]) for row in db.list_repos(user_id)]

    cache: dict[tuple[str, int], dict[str, Any] | None] = {}
    scans: list[_RepoScan] = []
    unreadable: list[str] = []
    for repo_name in repos:
        try:
            scans.append(await _scan_repo(repo_name, user_id, cache))
        except Exception:
            # A scoped call named this repo, so its failure is the answer. Unscoped, one
            # repo gone from the PAT's reach must not cost the worker every other backlog.
            if repo is not None:
                raise
            logger.warning("next_issue: could not read the backlog of %s", repo_name)
            unreadable.append(repo_name)

    blocked = [entry for scan in scans for entry in scan.blocked]

    candidates = [
        (index, scan.candidate) for index, scan in enumerate(scans) if scan.candidate is not None
    ]
    # Interrupt is the author's explicit override, so it outranks everything. Below it
    # there is no cross-repo order to appeal to, so registration order decides and
    # `also_eligible` shows what that arbitrary tie-break passed over.
    candidates.sort(key=lambda pair: (not pair[1]["interrupt"], pair[0], pair[1]["position"]))
    caveat = f" Could not read the backlog of {', '.join(unreadable)}." if unreadable else ""

    if candidates:
        chosen = candidates[0][1]
        explanation = (
            f"{chosen['repo']}#{chosen['number']} is marked interrupt, so it leads the backlog."
            if chosen["interrupt"]
            else f"{chosen['repo']}#{chosen['number']} is the highest-ordered ready issue "
            "with no open blockers."
        )
        result = {
            "issue": chosen,
            "reason": "selected",
            "explanation": explanation + caveat,
            "blocked": blocked,
        }
        if repo is None and len(candidates) > 1:
            result["also_eligible"] = [
                {"repo": other["repo"], "number": other["number"], "title": other["title"]}
                for _, other in candidates[1:]
            ]
        return result

    if blocked:
        reason = "all_blocked"
        explanation = (
            f"Every ready issue is blocked — {_describe_blocked(blocked)}. "
            "Unblocking one is the move here."
        )
    elif any(scan.saw_claimed for scan in scans):
        reason = "all_in_progress"
        explanation = f"Every ready, unblocked issue already carries {_IN_PROGRESS_LABEL}."
    elif any(scan.saw_open for scan in scans):
        reason = "none_ready"
        explanation = (
            "There are open issues but every one is parked — call set_backlog_flags "
            "with ready=True to unpark one."
        )
    elif not repos:
        reason = "empty"
        explanation = "No repos are registered with Lachesis, so there is no backlog to read."
    else:
        reason = "empty"
        explanation = "The backlog is empty — there are no open issues."

    return {
        "issue": None,
        "reason": reason,
        "explanation": explanation + caveat,
        "blocked": blocked,
    }


async def get_dashboard(
    status: str | None = None,
    include_inactive: bool = False,
    stale_after_days: int = groups.DEFAULT_STALE_AFTER_DAYS,
) -> dict[str, Any]:
    """Return cross-repo requirements summary, optionally filtered by status.

    Cancelled and superseded requirements are left out of the per-repo listings
    unless ``include_inactive`` is true or ``status`` names that state explicitly.
    ``by_status`` counts every requirement either way, so the summary stays a
    total of the whole corpus.

    Listed requirements carry the same ``derived`` block as
    :func:`list_requirements`; the ``status`` filter still selects on the
    recorded status.

    ``groups`` answers what shipped, what was dropped and what went quiet without
    a second call.  Every requirement read lands in exactly one of ``landed``,
    ``dropped``, ``stalled``, ``needs_attention``, ``diverged`` and ``in_flight``,
    tested in that order, and every group is present even when empty.  Grouping
    covers the whole corpus regardless of ``include_inactive`` — otherwise
    ``dropped`` would be empty by default — and is narrowed only by ``status``.
    A requirement counts as stalled after ``stale_after_days`` of silence unless
    an open blocker is itself still moving, which explains it.

    With a ``status`` filter the group counts sum to the matching requirements
    while ``summary.total`` keeps counting the whole corpus, as it always has.
    """
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(_invalid_status_message(status))
    if stale_after_days < 1:
        raise ValueError(f"stale_after_days must be at least 1, got {stale_after_days}")

    user_id = current_db_user_id.get()
    if not user_id:
        raise RuntimeError(
            "No DB user ID in request context. "
            "This may indicate a database connectivity issue (check server logs) "
            "or a token that predates the lachesis_uid claim."
        )

    repos = db.list_repos(user_id)
    aggregated: list[dict[str, Any]] = []
    all_candidates: list[groups.Candidate] = []
    status_counts: dict[str, int] = {}

    for repo_row in repos:
        repo_name = normalize_repo(repo_row["repo_name"])
        try:
            files = await github.list_files(repo_name, "requirements")
            requirements: list[dict[str, Any]] = []
            repo_candidates: list[groups.Candidate] = []
            seen_ids: set[str] = set()
            pending: _Pending = []
            landing_candidates: list[landing.Candidate] = []
            for f in files:
                if not f["name"].endswith(".md"):
                    continue
                content, _ = await github.get_file(repo_name, f["path"])
                post = frontmatter.loads(content)
                req_id = post["id"]
                seen_ids.add(req_id)
                req_status = canonical_status(post.get("status", ""))
                status_counts[req_status] = status_counts.get(req_status, 0) + 1
                if status is not None and req_status != status:
                    continue
                numbers = linked_issue_numbers(post)
                entry = {
                    "id": req_id,
                    "title": post["title"],
                    "status": req_status,
                    "github_issue": post.get("github_issue"),
                    "pr": None,
                    "warning": _entry_warning(None, numbers),
                    **_inactive_fields(post),
                }
                repo_candidates.append(groups.Candidate(repo_name, entry, numbers))
                if status is None and not include_inactive and req_status in INACTIVE_STATUSES:
                    continue
                requirements.append(entry)
                pending.append((entry, numbers, False))
                landing_candidates.append(landing.Candidate(f["path"], content, entry))

            lachesis_prs = await github.list_lachesis_prs(repo_name)
            for pr_info in lachesis_prs:
                branch = pr_info["branch"]
                pr = _pr_field(pr_info)
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
                    req_status = canonical_status(post.get("status", ""))
                    status_counts[req_status] = status_counts.get(req_status, 0) + 1
                    if status is not None and req_status != status:
                        continue
                    numbers = linked_issue_numbers(post)
                    entry = {
                        "id": req_id,
                        "title": post["title"],
                        "status": req_status,
                        "github_issue": post.get("github_issue"),
                        "pr": pr,
                        "warning": _entry_warning(pr, numbers),
                        **_inactive_fields(post),
                    }
                    repo_candidates.append(groups.Candidate(repo_name, entry, numbers))
                    if status is None and not include_inactive and req_status in INACTIVE_STATUSES:
                        continue
                    requirements.append(entry)
                    pending.append((entry, numbers, True))

            await _attach_derived(repo_name, pending)
            aggregated.append({"repo": repo_name, "requirements": requirements})
            all_candidates.extend(repo_candidates)
            # After the append, so a raise here could not cost the repo its listing
            # under this except's misleading log line. A status filter narrows the
            # candidates, which loses nothing: an unconsidered requirement is never
            # claimed, so a later unfiltered read still announces it.
            await landing.notify_landed(repo_name, landing_candidates)
        except Exception:
            logger.warning("get_dashboard: failed to fetch requirements from %s", repo_name)

    total = sum(status_counts.values())
    unlinked_count = sum(
        1
        for repo_entry in aggregated
        for req in repo_entry["requirements"]
        if not req["derived"]["linked_issues"]
    )
    grouped = groups.group_requirements(
        all_candidates, now=datetime.now(UTC), stale_after_days=stale_after_days
    )
    return {
        "repos": aggregated,
        "groups": grouped,
        "summary": {
            "total": total,
            "by_status": status_counts,
            "unlinked_count": unlinked_count,
            "groups": {name: len(grouped[name]) for name in groups.GROUP_NAMES},
            "stale_after_days": stale_after_days,
        },
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


async def get_pull_request(repo: str, pr_number: int) -> dict[str, Any]:
    """Return the current state of a pull request.

    Returns ``{"found": True, ...}`` with number, title, url, state, merged, merged_at,
    mergeable, mergeable_state, head_ref, base_ref and draft, or ``{"found": False}``
    when the pull request does not exist.
    """
    repo = await _resolve_repo(repo)
    pull = await github.get_pull_request(repo, pr_number)
    if pull is None:
        return {"found": False, "number": pr_number, "repo": repo}
    return {"found": True, **pull}


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
