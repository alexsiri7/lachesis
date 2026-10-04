"""MCP tool implementations for Lachesis requirements management."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, NamedTuple

import frontmatter
import httpx
import yaml

import db
import github
import groups
import landing
from auth import current_db_user_id
from derive import ActivityFailure, IssueFacts, derive, linked_issue_numbers
from spec_reference import SpecReference, append_reference, parse_reference
from status import INACTIVE_STATUSES, VALID_STATUSES, canonical_status

from .repos import _require_user_id, normalize_repo

logger = logging.getLogger(__name__)

_HOW_TO_TELL = """\
Lachesis has two workflows. A repository uses specs when its default branch contains
openspec/specs/ — list_capabilities reports uses_specs for it — and then follows the
spec-change workflow; any other repository follows the requirement-file workflow.
Call get_initial_instructions with repo to get only the workflow that applies there.
"""

_SPEC_WORKFLOW = """\
Spec-change workflow, for a repository that uses specs — follow these steps in order:

1. list_capabilities / get_capability — read the capabilities the work touches, and the
   open spec changes already editing them.
2. propose_spec_change — open a spec change editing those capabilities, with the why as
   its description. update_spec_change edits it until it merges.
3. create_github_issues — each issue names its capability, the spec change it implements
   (spec_change: PR number) and optionally the requirement within the capability. There
   is no linking step: an issue states what it implements when it is created.

Work that brings the code in line with the spec as it already stands is a spec gap: create
its issue with its capability and spec_gap: true instead of a spec change.
create_requirement is refused in a repository that uses specs.
"""

_REQUIREMENT_WORKFLOW = """\
Requirement-file workflow, for a repository without specs — follow these steps in order:

1. create_requirement — capture intent (title, why, what). Requirement starts as 'draft'.
2. create_github_issues — break it into implementation tasks. Pass issues with title and body.
3. link_issues — attach the issue numbers to the requirement. Status advances to 'idea'.

A requirement without linked issues is a draft. Archon will not act on drafts.
"""

_BACKLOG = """\
New issues are ready, eligible for autonomous pickup as soon as they are created. Call
set_backlog_flags with ready=False to park an issue that is knowingly unfinished, and
ready=True to unpark it. The backlog order is computed, never set by hand: interrupt,
then security work (issues labelled security, for security incidents and security audit
findings), then the author's focus, then work on active milestones nearest done first,
then everything else, older first within each. Work ready and unblocked for two whole
sprints moves up a tier. The author steers with set_focus and clear_focus on a repository
or milestone, and the interrupt flag for the one thing that must jump the queue. Call
next_issue to ask what to pick up — it returns the top ready, unblocked issue, or
explains why there is none.

Before asking anything, call search_answered_questions with the repository and the words
of your question. If an answer already settles it, act on that answer instead of asking
again.
When you cannot tell what the spec intends for an issue, do not guess: call ask_author
with your question and move on to other work. The issue is labelled needs-author and
next_issue passes over it, keeping its computed place, until answer_author posts the
answer and removes the label, once no other open question blocks the issue.
For any other decision only the author can make — a spec contradiction, large work,
maturity or an improvement — or one that blocks several issues, call ask_question.
answer_question records the author's answer to a question by id, with its outcome, and
releases every issue it blocked that no other open question still blocks.
When spending on models outside the Claude allowance reaches its cap, ask a budget
question saying in its context what the extra spend would deliver; it blocks no issue, so
keep working within the cap.

At the end of every run on an issue, call report_usage with the issue, the model, tokens
in and out, and the kind of work: triage, summarising, audit, tests, implementation or
spec work. Give its cost in dollars for a model paid per token, such as an overflow run
through Requesty; leave it out for a run on the Claude allowance.

When review sends a run's work back for changes, when you retry a failed run, or when an
audit finds a gap in work done on an issue, call report_run_outcome with the issue, the
kind of work and the outcome: rework, retry or audit_finding. It counts against the latest
run of that kind on the issue, so report a retry at the start of the retrying run.
read_scorecard shows, for each model and kind of work, its runs and failures, whether it is
trusted, and whether it may trial that kind.

Every issue carries an estimate in tokens. When you create or triage an issue, call
estimate_issue with the issue and the kind of work it mostly is; the estimate comes from
past issues of that kind and capability, and is low confidence until there are five.

At the start of every run, call report_fuel with the Claude allowance used this week, when
it resets and any shorter limit in force, and the overflow spent this week against the
Requesty cap. read_fuel returns what is left, the 20 percent reserve kept for security work
and production incidents, and the pace for today.

Agents do not message each other: they leave notes on the work. When you stop working on
an issue, finished or not, call add_note on it with type handoff, saying what you did and
what is left, so the next agent starts from what you knew. Use finding, progress, decision
and report notes for the rest. Notes are never edited; a correction is a new note.
next_issue returns the offered issue's notes with it, oldest first; list_notes reads the
notes on any issue or milestone.

When a session with the author starts,
call list_open_questions and get_dashboard before anything else. Raise every open
question with the author first, oldest first — the same queue agents read — then every
issue in get_dashboard's waiting_on_author list that none of those questions blocks: it
was labelled needs-author with no question recorded.
"""

_MILESTONE_OWNER = """\
Milestone owner, for an unattended agent run on a schedule in repositories that use specs.
On each run, call list_repos, then list_milestones for each repository, and call
review_milestone on every milestone that is active or verifying. Close its gaps:
- a requirement no issue names: create_github_issues with an issue naming its spec change,
  capability and requirement;
- an issue closed without a merged pull request: create_github_issues with a spec gap
  (spec_gap: true) for the work it dropped;
- a gap you cannot close without the author: ask_question about the milestone, and move on.
Merges not yet in production need nothing from you. Then call add_note on the milestone
with type progress, saying what you found and what you did.
The milestone owner never edits the spec: it never calls propose_spec_change or
update_spec_change. When a milestone is done, Lachesis itself sends the author one push
notification and adds a report note to the milestone.
"""


async def get_initial_instructions(repo: str | None = None) -> str:
    """Return the Lachesis workflow for new Claude sessions.

    With *repo*, only the workflow that applies there; without, both and how to
    tell which applies. Wherever the spec workflow applies, so does the milestone
    owner's.
    """
    # specs imports this module, so it cannot be imported at the top.
    from .specs import uses_specs

    if repo is None:
        workflows = [_HOW_TO_TELL, _SPEC_WORKFLOW, _REQUIREMENT_WORKFLOW]
    elif await uses_specs(await _resolve_repo(repo)):
        workflows = [_SPEC_WORKFLOW]
    else:
        workflows = [_REQUIREMENT_WORKFLOW]
    owner = [_MILESTONE_OWNER] if _SPEC_WORKFLOW in workflows else []
    return "\n".join([*workflows, _BACKLOG, *owner])


async def _resolve_repo(repo: str, *, require_registered: bool = False) -> str:
    """Resolve a short name (e.g. 'lachesis') to a registered 'owner/repo' string.

    If the input already contains '/' (or is a full URL) it is normalised and
    returned as-is.  Otherwise the user's registered repos are searched for one
    whose final path component matches the short name.  Falls back to the raw
    input if no match is found or if there is no auth context.

    With *require_registered*, the repo must be one the current user has
    registered (matched by short name or full name); otherwise ``ValueError``.
    """
    try:
        normalized = normalize_repo(repo)
    except ValueError:
        normalized = repo

    if "/" in normalized and not require_registered:
        return normalized

    user_id = _require_user_id() if require_registered else current_db_user_id.get()
    if user_id:
        rows = db.list_repos(user_id)
        for row in rows:
            try:
                full = normalize_repo(row["repo_name"])
            except ValueError:
                continue
            if normalized.lower() in (full.lower(), full.split("/")[-1].lower()):
                return full

    if require_registered:
        raise ValueError(f"Repository {normalized!r} is not registered")
    return normalized


class _QuotedId(str):
    """A requirement id YAML must write quoted.

    Bare, YAML 1.1 reads ``020`` as the octal int 16, and ``008`` as a string
    only because it is not valid octal.
    """


class _RequirementDumper(yaml.SafeDumper):
    pass


_RequirementDumper.add_representer(
    _QuotedId,
    lambda dumper, value: dumper.represent_scalar("tag:yaml.org,2002:str", value, style="'"),
)


def _requirement_id(path: str, post: frontmatter.Post) -> str:
    """The id of the requirement file at *path*, as written.

    The filename's numeric prefix wins over the frontmatter: it is what
    :func:`_locate_requirement` resolves by, and an id YAML already read as an
    int cannot be turned back into its zero-padded text.
    """
    prefix = path.rsplit("/", 1)[-1].split("-", 1)[0]
    if prefix.isdigit():
        return prefix
    return str(post["id"])


def _dump_requirement(path: str, post: frontmatter.Post) -> str:
    """Serialize the requirement file at *path* with its id quoted."""
    post["id"] = _QuotedId(_requirement_id(path, post))
    return frontmatter.dumps(post, Dumper=_RequirementDumper)


async def _locate_requirement(repo: str, id: str) -> tuple[str, str, str, dict[str, Any] | None]:
    """Locate a requirement file on its open Lachesis PR branch, else on the default branch.

    Returns ``(path, content, sha, pr)``.  *pr* is ``None`` for the default branch,
    otherwise the open pull request the file was read from.  An open
    ``lachesis/req-{id}-`` pull request wins even when the default branch also has
    the file: that is where the requirement's pending edits live.  A pull request
    without the file is a pending deletion, and the default branch still answers
    for the requirement until it merges.  Raises :class:`FileNotFoundError` if not
    found.
    """
    pr = await github.find_open_pr(repo, f"lachesis/req-{id}-")
    branch = pr["branch"] if pr else None
    files = await github.list_files(repo, "requirements", branch=branch)
    matching = [f for f in files if f["name"].startswith(f"{id}-")]
    if not matching and pr is not None:
        pr, branch = None, None
        files = await github.list_files(repo, "requirements")
        matching = [f for f in files if f["name"].startswith(f"{id}-")]
    if not matching:
        raise FileNotFoundError(f"Requirement {id!r} not found in {repo!r}")
    path = matching[0]["path"]
    content, sha = await github.get_file(repo, path, branch=branch)
    return path, content, sha, pr


_MAX_WRITE_ATTEMPTS = 3


class _Written[T](NamedTuple):
    """What a write produced, and the open pull request it landed on.

    *pr* is ``None`` when the write reached the default branch.
    """

    result: T
    pr: dict[str, Any] | None


async def _write_requirement[T](
    repo: str,
    id: str,
    change: str,
    write: Callable[[str, str, str, str | None], Awaitable[T]],
) -> _Written[T]:
    """Locate requirement *id* and hand ``(path, content, sha, branch)`` to *write*.

    The Contents API answers a write carrying a sha that is no longer the file's
    current one with 409, and documents re-reading as the recovery.  Anything that
    invalidates the sha between the read and the write therefore costs a fresh
    locate and another attempt rather than the caller's whole tool call.

    The same 409 is also GitHub's answer to a direct commit on a protected default
    branch, which no re-read can clear.  A default-branch write that is still
    conflicting on the last attempt is landed on a fresh branch and a pull request
    instead, as ``create_requirement`` already does for a protected ``main``.
    *change* names the edit in that pull request's title.

    A pull request already open for the requirement at that point is one the
    locate could not write to — a pending deletion, whose branch no longer has
    the file — or one opened since.  Either way the write is refused rather than
    landed on a second pull request racing the first.
    """
    for attempt in range(1, _MAX_WRITE_ATTEMPTS + 1):
        path, content, sha, pr = await _locate_requirement(repo, id)
        branch = pr["branch"] if pr else None
        try:
            return _Written(await write(path, content, sha, branch), pr)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            if attempt < _MAX_WRITE_ATTEMPTS:
                logger.warning(
                    "requirement write conflicted repo=%s id=%s attempt=%d/%d",
                    repo,
                    id,
                    attempt,
                    _MAX_WRITE_ATTEMPTS,
                )
                continue
            if pr is not None:
                raise RuntimeError(
                    f"Could not write {path} in {repo!r}: GitHub reported a conflict on "
                    f"{_MAX_WRITE_ATTEMPTS} consecutive attempts, each against a freshly "
                    f"read version of the file on branch {branch!r}. Something else is "
                    "changing that file concurrently; retry once it settles."
                ) from exc
            open_pr = await github.find_open_pr(repo, f"lachesis/req-{id}-")
            if open_pr is not None:
                raise RuntimeError(
                    f"Could not write {path} in {repo!r}: the default branch refuses direct "
                    f"writes and pull request #{open_pr['pr_number']} ({open_pr['pr_url']}) "
                    "is already open for this requirement. Merge or close it, then retry."
                ) from exc
            logger.warning(
                "requirement write on the default branch still conflicts after %d attempts "
                "repo=%s id=%s; landing it on a pull request",
                _MAX_WRITE_ATTEMPTS,
                repo,
                id,
            )
            return await _write_via_pull_request(repo, id, path, content, change, write)
    raise AssertionError("unreachable: the last attempt either returns or raises")


async def _write_via_pull_request[T](
    repo: str,
    id: str,
    path: str,
    content: str,
    change: str,
    write: Callable[[str, str, str, str | None], Awaitable[T]],
) -> _Written[T]:
    """Apply *write* to *path* on a new branch off ``main`` and open a pull request for it.

    The branch name carries a timestamp so that it never collides with the branch a
    previous pull request for this requirement left behind.
    """
    stem = path.removeprefix("requirements/").removesuffix(".md")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    branch = f"lachesis/req-{stem}-{stamp}"
    title = frontmatter.loads(content).get("title", "")

    async def commit() -> T:
        branch_content, branch_sha = await github.get_file(repo, path, branch=branch)
        return await write(path, branch_content, branch_sha, branch)

    result, pr = await _open_pull_request(
        repo,
        branch,
        title=f"[Lachesis] {change}",
        body=(
            f"This pull request was automatically created by Lachesis.\n\n"
            f"**Requirement:** {id} — {title}\n"
            f"**File:** `{path}`\n\n"
            f"Review and merge to apply this change to `main`."
        ),
        commit=commit,
    )
    return _Written(
        result,
        {
            "branch": branch,
            "pr_number": pr["number"],
            "pr_url": pr["url"],
            "created_at": pr["created_at"],
        },
    )


async def _open_pull_request[T](
    repo: str, branch: str, *, title: str, body: str, commit: Callable[[], Awaitable[T]]
) -> tuple[T, dict[str, Any]]:
    """Create *branch* off ``main``, run *commit* on it, and open a pull request for it.

    Returns ``(commit result, pull request)``.  Automerge is enabled best-effort: the
    change has already landed on the branch, so a failure there is logged and the
    pull request is returned for a manual merge.
    """
    sha = await github.get_branch_sha(repo, "main")
    await github.create_branch(repo, branch, sha)
    try:
        result = await commit()
        pr = await github.create_pull_request(
            repo, title=title, body=body, head=branch, base="main"
        )
    except Exception:
        logger.warning(
            "pull request fallback failed mid-flight repo=%s branch=%s; branch may be orphaned",
            repo,
            branch,
        )
        raise
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
                    "automerge enabled but delete_branch_on_merge is disabled on repo=%s "
                    "— branch will not auto-delete",
                    repo,
                )
        else:
            logger.info(
                "automerge not available on repo=%s (allow_auto_merge=False) "
                "— user must merge manually",
                repo,
            )
    except Exception as exc:
        logger.warning(
            "enable_automerge failed repo=%s pr=%s error=%r; pr_url returned for manual merge",
            repo,
            pr["number"],
            exc,
        )
    return result, pr


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

    ``created_at`` is when the PR was opened, and ``None`` when GitHub did not report it.
    """
    if pr_info is None:
        return None
    return {
        "number": pr_info["pr_number"],
        "state": "open",
        "merged": False,
        "url": pr_info["pr_url"],
        "branch": pr_info["branch"],
        "created_at": pr_info.get("created_at"),
    }


def _entry_warning(pr: dict[str, Any] | None, numbers: list[int]) -> str | None:
    """An unmerged PR is reported even when issues are linked — merging it unblocks them."""
    if pr is not None:
        return f"awaiting merge of PR #{pr['number']}"
    return None if numbers else "no linked issues"


_Copies = dict[str, tuple[str, dict[str, Any] | None]]
"""The authoritative copy of each requirement file, keyed by path.

The value is ``(content, pr)`` — *pr* the open Lachesis pull request the copy was
read from, ``None`` for the default branch.
"""


async def _read_default_branch(repo: str, files: list[dict[str, Any]]) -> _Copies:
    """Read every requirement file the default branch lists, in listing order."""
    copies: _Copies = {}
    for f in files:
        if not f["name"].endswith(".md"):
            continue
        content, _ = await github.get_file(repo, f["path"])
        copies[f["path"]] = (content, None)
    return copies


def _is_pending_edit(branch: str, name: str) -> bool:
    """Whether a Lachesis pull request branch carries the pending edit of file *name*.

    Every fallback producer names its branch ``lachesis/req-{id}-...`` after the
    ``{id}-...md`` file it edits — the same prefix :func:`_locate_requirement`
    resolves by.  A branch carrying some other requirement's file unchanged from
    the default branch is not that requirement's pending edit.
    """
    req_id = branch.removeprefix("lachesis/req-").split("-", 1)[0]
    return name.startswith(f"{req_id}-")


async def _read_pull_request_branch(repo: str, pr_info: dict[str, Any], copies: _Copies) -> None:
    """Add the requirement files an open Lachesis pull request carries to *copies*.

    The pull request's own pending edit replaces the default branch's copy of that
    file, keeping its position; a file that is not yet on the default branch is
    appended.  A file the branch merely carries along from the default branch is
    not read again: the default branch already served it.  Nor is one an earlier
    pull request in the list supplied: the first listed is the one
    :func:`github.find_open_pr`, and so :func:`_locate_requirement`, resolves.
    """
    branch = pr_info["branch"]
    pr_files = await github.list_files(repo, "requirements", branch=branch)
    for f in pr_files:
        if not f["name"].endswith(".md"):
            continue
        if f["path"] in copies:
            _, served_by = copies[f["path"]]
            if served_by is not None or not _is_pending_edit(branch, f["name"]):
                continue
        content, _ = await github.get_file(repo, f["path"], branch=branch)
        copies[f["path"]] = (content, pr_info)


_Pending = list[tuple[dict[str, Any], list[int], bool]]


async def _attach_derived(repo: str, pending: _Pending) -> None:
    """Derive a status for each pending entry from one batch of issue activity.

    A repo whose requirements link no issues costs no request.  GitHub failing
    here must not cost the caller the listing it actually asked for, so an entry
    whose issues went unread reports an unavailable derivation naming the cause
    instead of raising, and the entries whose issues did come back derive
    normally alongside it.
    """
    numbers = sorted({number for _, entry_numbers, _ in pending for number in entry_numbers})
    facts: dict[int, IssueFacts] = {}
    failure: ActivityFailure | None = None
    if numbers:
        try:
            activity = await github.get_issue_activity(repo, numbers)
            facts, failure = activity["facts"], activity["failure"]
        except Exception as exc:
            logger.warning(
                "failed to fetch issue activity for repo=%s error=%r; status not derived",
                repo,
                exc,
            )
            failure = "transient"
    for entry, entry_numbers, on_pr_branch in pending:
        entry["derived"] = derive(
            entry["status"], entry_numbers, facts, on_pr_branch=on_pr_branch, failure=failure
        )


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

    A requirement is read from its open Lachesis pull request when it has one, as
    :func:`get_requirement` reads it, and from the default branch otherwise.
    """
    repo = await _resolve_repo(repo)
    files = await github.list_files(repo, "requirements")
    copies = await _read_default_branch(repo, files)

    try:
        lachesis_prs = await github.list_lachesis_prs(repo)
    except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
        logger.warning(
            "list_requirements: PR-branch scan failed repo=%s error=%r; "
            "reporting default-branch requirements only",
            repo,
            exc,
        )
        lachesis_prs = []
    for open_pr in lachesis_prs:
        await _read_pull_request_branch(repo, open_pr, copies)

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    pending: _Pending = []
    landing_candidates: list[landing.Candidate] = []
    for path, (content, pr_info) in copies.items():
        post = frontmatter.loads(content)
        req_id = _requirement_id(path, post)
        if pr_info is not None and req_id in seen_ids:
            continue
        seen_ids.add(req_id)
        req_status = canonical_status(post.get("status", ""))
        if not include_inactive and req_status in INACTIVE_STATUSES:
            continue
        numbers = linked_issue_numbers(post)
        pr = _pr_field(pr_info)
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
        pending.append((entry, numbers, pr is not None))
        if pr is None:
            landing_candidates.append(landing.Candidate(path, content, entry))

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

    Refused in a repository that uses specs, where work starts as a spec change.
    """
    # specs imports this module, so it cannot be imported at the top.
    from .specs import uses_specs

    repo = await _resolve_repo(repo)
    if await uses_specs(repo):
        raise ValueError(
            f"{repo} uses specs, so it takes no new requirement files. "
            "Propose a spec change with propose_spec_change instead."
        )
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
    content = _dump_requirement(path, post)

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

        async def commit() -> None:
            await github.create_file(
                repo, path, content, f"feat: add requirement {id_str}", branch=branch_name
            )

        _, pr = await _open_pull_request(
            repo,
            branch_name,
            title=f"[Lachesis] Add requirement {id_str}: {title}",
            body=(
                f"This pull request was automatically created by Lachesis.\n\n"
                f"**Requirement:** {id_str} — {title}\n"
                f"**File:** `{path}`\n\n"
                f"Review and merge to add this requirement to `main`."
            ),
            commit=commit,
        )
        pr_url = pr["url"]
    result: dict[str, Any] = {"id": id_str, "path": path}
    if pr_url is not None:
        result["pr_url"] = pr_url
    return result


def _invalid_status_message(value: object, accepted: frozenset[str] = VALID_STATUSES) -> str:
    """Explain a rejected status, naming the canonical spelling when there is one."""
    hint = canonical_status(value)
    correction = f" — use {hint!r}" if hint in accepted else ""
    return f"Invalid status {value!r}{correction}. Must be one of {sorted(accepted)}"


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


async def _normalize_superseded_by(value: object, repo: str) -> dict[str, str | int]:
    """Validate the replacement reference and fill in the repo it defaults to.

    The replacement is either a requirement, ``{"id", "repo"}``, or a spec change,
    ``{"spec_change": <pr number>, "repo"}``.
    """
    if (
        not isinstance(value, dict)
        or len({"id", "spec_change"} & value.keys()) != 1
        or value.keys() - {"id", "spec_change", "repo"}
    ):
        raise ValueError(
            "'superseded_by' must be a mapping with either 'id' (a requirement) or "
            f"'spec_change' (a pull request number), and optional 'repo', got {value!r}"
        )
    target_repo = value.get("repo")
    resolved = await _resolve_repo(target_repo) if target_repo else repo
    if "spec_change" in value:
        number = value["spec_change"]
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError(f"'spec_change' must be a pull request number, got {number!r}")
        return {"repo": resolved, "spec_change": number}
    raw_id = value.get("id")
    if isinstance(raw_id, int) and not isinstance(raw_id, bool):
        req_id = f"{raw_id:03d}"
    elif isinstance(raw_id, str) and raw_id.strip():
        req_id = raw_id.strip()
    else:
        raise ValueError(f"'superseded_by' needs a non-empty 'id', got {raw_id!r}")
    return {"repo": resolved, "id": req_id}


async def update_requirement(repo: str, id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Update fields (title, why, what, status, reason, superseded_by) in a requirement.

    Setting ``status`` to ``cancelled`` requires a ``reason``; setting it to
    ``superseded`` requires ``superseded_by``: another requirement as
    ``{"id": ..., "repo": ...}`` or a spec change as ``{"spec_change": <pr number>,
    "repo": ...}``, where ``repo`` defaults to the requirement's own repo.

    Returns ``{"content", "pr"}`` in the shape of :func:`get_requirement`: the
    updated file, and the open pull request the update awaits merge on, or ``None``
    when it landed on the default branch.
    """
    repo = await _resolve_repo(repo)

    async def write(path: str, content: str, sha: str, branch: str | None) -> str:
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
        resolved_fields = fields
        if "superseded_by" in fields:
            resolved_fields = {
                **fields,
                "superseded_by": await _normalize_superseded_by(fields["superseded_by"], repo),
            }

        for key in ("title", "status", "reason", "superseded_by"):
            if key in resolved_fields:
                post[key] = resolved_fields[key]
        if resulting_status != "cancelled":
            post.metadata.pop("reason", None)
        if resulting_status != "superseded":
            post.metadata.pop("superseded_by", None)

        body = post.content
        for section, key in (("Why", "why"), ("What", "what")):
            if key not in resolved_fields:
                continue
            new_block = f"## {section}\n\n{resolved_fields[key]}\n"
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

        updated_content = _dump_requirement(path, post)
        await github.update_file(
            repo, path, updated_content, sha, f"chore: update requirement {id}", branch=branch
        )
        return updated_content

    written = await _write_requirement(repo, id, f"Update requirement {id}", write)
    return {"content": written.result, "pr": _pr_field(written.pr)}


async def delete_requirement(repo: str, id: str) -> dict[str, Any]:
    """Delete a requirement. Refuses if status is 'done'.

    Returns ``{"deleted": path, "pr"}`` — *pr* is the open pull request the deletion
    awaits merge on, or ``None`` when it landed on the default branch.
    """
    repo = await _resolve_repo(repo)

    async def write(path: str, content: str, sha: str, branch: str | None) -> str:
        post = frontmatter.loads(content)
        if canonical_status(post.get("status")) == "done":
            raise ValueError(
                "Cannot delete a done requirement — it represents implemented code. "
                "Create a new requirement to remove the feature."
            )

        await github.delete_file(repo, path, sha, f"chore: delete requirement {id}", branch=branch)
        return path

    written = await _write_requirement(repo, id, f"Delete requirement {id}", write)
    return {"deleted": written.result, "pr": _pr_field(written.pr)}


async def link_issues(repo: str, id: str, issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Write GitHub issue numbers into the requirement's Issues section.

    Returns ``{"content", "pr"}`` in the shape of :func:`get_requirement`: the
    updated file, and the open pull request the link awaits merge on, or ``None``
    when it landed on the default branch.
    """
    repo = await _resolve_repo(repo)

    async def write(path: str, content: str, sha: str, branch: str | None) -> str:
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
        updated_content = _dump_requirement(path, post)
        await github.update_file(
            repo,
            path,
            updated_content,
            sha,
            f"chore: link issues to requirement {id}",
            branch=branch,
        )
        return updated_content

    written = await _write_requirement(repo, id, f"Link issues to requirement {id}", write)
    return {"content": written.result, "pr": _pr_field(written.pr)}


def _apply_backlog_overrides(
    repo: str, issues: list[dict[str, Any]], results: list[dict[str, Any]]
) -> None:
    """Apply per-issue ``ready`` overrides to created issues.

    Best-effort throughout: the GitHub issues already exist and cannot be
    un-created, so every failure is logged and reported through the result dict.
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


SPEC_GAP_LABEL = "spec-gap"
_SPEC_GAP_COLOR = "d93f0b"


def _spec_reference_problems(issue: dict[str, Any]) -> list[str]:
    problems = []
    spec_change = issue.get("spec_change")
    spec_gap = issue.get("spec_gap", False)
    if spec_change is not None and (
        isinstance(spec_change, bool) or not isinstance(spec_change, int)
    ):
        problems.append("spec_change as a pull request number")
    if not isinstance(spec_gap, bool):
        problems.append("spec_gap as true or false")
    if (spec_change is None) == (spec_gap is not True):
        problems.append("exactly one of spec_change or spec_gap: true")
    capability = issue.get("capability")
    if not isinstance(capability, str) or not capability.strip():
        problems.append("capability")
    elif _is_multiline(capability):
        problems.append("capability on one line")
    requirement = issue.get("requirement")
    if requirement is not None and not isinstance(requirement, str):
        problems.append("requirement as a string")
    elif requirement is not None and _is_multiline(requirement):
        problems.append("requirement on one line")
    return problems


def _is_multiline(value: str) -> bool:
    return "\n" in value or "\r" in value


def _spec_references(issues: list[dict[str, Any]]) -> list[SpecReference]:
    """Read every issue's spec reference, refusing the whole batch if any is incomplete."""
    missing = [
        f"issue {index} ({issue.get('title')!r}) needs {', '.join(problems)}"
        for index, issue in enumerate(issues, start=1)
        if (problems := _spec_reference_problems(issue))
    ]
    if missing:
        raise ValueError(
            "This repository uses specs, so every issue must name the spec change it "
            "implements (spec_change: pull request number) or be a spec gap "
            "(spec_gap: true), and name its capability. No issues were created: "
            + "; ".join(missing)
        )
    return [
        SpecReference(
            spec_change=issue.get("spec_change"),
            capability=issue["capability"].strip(),
            requirement=(issue.get("requirement") or "").strip() or None,
        )
        for issue in issues
    ]


async def create_github_issues(
    repo: str, issues: list[dict[str, Any]], project_id: str | None = None
) -> list[dict[str, Any]]:
    """Create one or more GitHub issues and return their numbers/titles/urls.

    Issues are created ready; the backlog orders them. Passing ``ready: false``
    parks an issue, holding it back from autonomous pickup. ``before`` / ``after``
    are refused: the backlog order is computed, never set by hand.

    In a repository that uses specs, each issue must also carry ``capability`` and
    exactly one of ``spec_change`` (pull request number) or ``spec_gap: true``, and
    may carry ``requirement``; the reference is appended to its body and spec gaps
    are labelled ``spec-gap``. Elsewhere these fields are ignored.
    """
    # specs imports this module, so it cannot be imported at the top.
    from .specs import uses_specs

    if any(key in issue for issue in issues for key in ("before", "after")):
        raise ValueError(
            "before/after are no longer accepted: the backlog order is computed. "
            "Steer it with set_focus or the interrupt flag. No issues were created."
        )
    repo = await _resolve_repo(repo)
    references: list[SpecReference | None] = [None] * len(issues)
    if issues and await uses_specs(repo):
        references = list(_spec_references(issues))
        if any(ref.spec_change is None for ref in references if ref):
            await github.ensure_label(repo, SPEC_GAP_LABEL, _SPEC_GAP_COLOR)

    # Phase 1: Create all issues, build number→id map
    results: list[dict[str, Any]] = []
    number_to_id: dict[int, int] = {}
    for issue, reference in zip(issues, references, strict=True):
        body = issue["body"]
        labels = issue.get("labels")
        if reference is not None:
            body = append_reference(body, reference)
            if reference.spec_change is None and SPEC_GAP_LABEL not in (labels or []):
                labels = [*(labels or []), SPEC_GAP_LABEL]
        result = await github.create_issue(repo, issue["title"], body, labels)
        node_id = result.pop("node_id")
        results.append(
            {
                **result,
                "blocked_by": [],
                "added_to_project": False,
                "ready": True,
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
    if any("ready" in issue for issue in issues):
        _apply_backlog_overrides(repo, issues, results)

    return results


# ---------------------------------------------------------------------------
# Backlog ordering
# ---------------------------------------------------------------------------


SECURITY_LABEL = "security"
_IN_PROGRESS_LABEL = "archon:in-progress"
_NEEDS_AUTHOR_LABEL = "needs-author"
_NEEDS_AUTHOR_COLOR = "fbca04"

_TIERS = ("interrupt", "security", "focus", "milestone", "other")
_INTERRUPT, _SECURITY, _FOCUS, _MILESTONE, _OTHER = range(len(_TIERS))
_SPRINT = timedelta(days=7)
_SPRINTS_TO_AGE = 2
# Sorts after every active milestone, so issues outside the milestone tier, or aged
# into it, fall back to age alone.
_NO_MILESTONE = (math.inf, math.inf, math.inf)

_PUBLIC_BACKLOG_KEYS = ("number", "title", "position", "tier", "aged", "ready", "interrupt")


def _public_backlog(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project internal backlog entries onto the documented backlog response shape.

    `_backlog_order` also carries each issue's labels, creation time and sort key,
    which `next_issue` reads to skip work in progress and to merge repos; they are
    not part of what the backlog tools promise their callers.
    """
    return [{key: item[key] for key in _PUBLIC_BACKLOG_KEYS} for item in items]


def _sprint_start(resets_at: datetime, now: datetime) -> datetime:
    """Return when the sprint holding *now* started: sprints start at each weekly reset.

    *resets_at* is any one reset, past or future.
    """
    return resets_at + ((now - resets_at) // _SPRINT) * _SPRINT


async def _unblocked_since(repo: str, number: int, since: datetime) -> datetime | None:
    """Return when *number* last became unblocked, no earlier than *since*.

    ``None`` when a blocker is still open.
    """
    blockers = await github.list_blockers(repo, number) or []
    if any(blocker["state"] != "closed" for blocker in blockers):
        return None
    closed = [
        datetime.fromisoformat(b["closed_at"]) for b in blockers if b["closed_at"] is not None
    ]
    return max([since, *closed])


async def _backlog_order(repo: str, user_id: str, now: datetime) -> list[dict[str, Any]]:
    """Return the repo's open issues in computed backlog order.

    Tiers, highest first: interrupt; security (labelled ``security``); the author's
    focus, on the repo or on the milestone holding the issue's spec change; work on an
    active milestone, the one with fewest open issues first; everything else. Older
    issues come first within a tier. A ready issue that has waited, unblocked and not
    in progress, for two whole sprints moves up one tier — interrupt and security work
    never age. Sprints start at each weekly allowance reset, so with no fuel reported
    nothing ages.

    An issue with no backlog row is ready: a row exists only to park or interrupt one.
    Closed issues drop out here; their rows stay, so the flags survive a close and
    reopen. Each entry's ``position`` is its index in this order.
    """
    open_issues = await github.list_open_issues(repo)
    rows = {row["issue_number"]: row for row in db.list_backlog(user_id, repo)}
    milestones = [m for m in db.list_milestones(user_id, repo) if m["dropped_at"] is None]
    focuses = [focus for focus in db.list_focus(user_id) if focus["repo_name"] == repo]
    fuel = db.get_fuel(user_id)

    repo_focused = any(focus["milestone_id"] is None for focus in focuses)
    focused_ids = {focus["milestone_id"] for focus in focuses}
    milestone_of = {change: m for m in milestones for change in m["spec_changes"]}

    items: list[dict[str, Any]] = []
    for issue in open_issues:
        number = issue["number"]
        row = rows.get(number)
        reference = parse_reference(issue["body"])
        milestone = milestone_of.get(reference.spec_change) if reference else None
        created_at = datetime.fromisoformat(issue["created_at"])
        ready_since = row["ready_since"] if row is not None else None
        if row is not None and row["interrupt"]:
            tier = _INTERRUPT
        elif SECURITY_LABEL in issue["labels"]:
            tier = _SECURITY
        elif repo_focused or (milestone is not None and milestone["id"] in focused_ids):
            tier = _FOCUS
        elif milestone is not None and milestone["started_at"] is not None:
            tier = _MILESTONE
        else:
            tier = _OTHER
        items.append(
            {
                "number": number,
                "title": issue["title"],
                "labels": issue["labels"],
                "created_at": created_at,
                "waiting_since": max(created_at, ready_since or created_at),
                "milestone": milestone,
                "tier_rank": tier,
                "aged": False,
                "ready": bool(row["ready"]) if row is not None else True,
                "interrupt": bool(row["interrupt"]) if row is not None else False,
            }
        )

    if fuel is not None:
        threshold = _sprint_start(fuel["weekly_resets_at"], now) - _SPRINTS_TO_AGE * _SPRINT
        waiting = [
            item
            for item in items
            if item["tier_rank"] > _SECURITY
            and item["ready"]
            and _IN_PROGRESS_LABEL not in item["labels"]
            and _NEEDS_AUTHOR_LABEL not in item["labels"]
            and item["waiting_since"] <= threshold
        ]
        unblocked_since = await asyncio.gather(
            *(_unblocked_since(repo, item["number"], item["waiting_since"]) for item in waiting)
        )
        for item, since in zip(waiting, unblocked_since, strict=True):
            if since is not None and since <= threshold:
                item["tier_rank"] -= 1
                item["aged"] = True

    open_in_milestone = Counter(item["milestone"]["id"] for item in items if item["milestone"])
    for item in items:
        milestone = item.pop("milestone")
        del item["waiting_since"]
        milestone_key = _NO_MILESTONE
        if item["tier_rank"] == _MILESTONE and not item["aged"]:
            milestone_key = (
                float(open_in_milestone[milestone["id"]]),
                milestone["started_at"].timestamp(),
                float(milestone["id"]),
            )
        item["sort_key"] = (item["tier_rank"], milestone_key, item["created_at"], item["number"])
        item["tier"] = _TIERS[item.pop("tier_rank")]

    items.sort(key=lambda item: item["sort_key"])
    for position, item in enumerate(items):
        item["position"] = position
    return items


async def get_backlog(repo: str) -> list[dict[str, Any]]:
    """Return the repo's open issues in computed backlog order.

    Each entry has ``number``, ``title``, ``position``, ``tier`` (one of interrupt,
    security, focus, milestone, other), ``aged`` (moved up a tier for waiting two
    whole sprints), ``ready`` and ``interrupt``.
    """
    repo = await _resolve_repo(repo)
    user_id = _require_user_id()
    return _public_backlog(await _backlog_order(repo, user_id, datetime.now(UTC)))


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


class _RepoScan(NamedTuple):
    """What one repo's backlog walk found for `next_issue`."""

    candidate: dict[str, Any] | None
    blocked: list[dict[str, Any]]
    saw_open: bool
    saw_claimed: bool
    saw_waiting: bool


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
    repo: str,
    user_id: str,
    now: datetime,
    cache: dict[tuple[str, int], dict[str, Any] | None],
) -> _RepoScan:
    """Walk one repo's backlog for the first pickable issue, reporting what it passed."""
    order = await _backlog_order(repo, user_id, now)
    open_titles = {item["number"]: item["title"] for item in order}
    candidate: dict[str, Any] | None = None
    blocked: list[dict[str, Any]] = []
    saw_claimed = False
    saw_waiting = False

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
        if _NEEDS_AUTHOR_LABEL in item["labels"]:
            saw_waiting = True
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
                "tier": item["tier"],
                "interrupt": item["interrupt"],
                "sort_key": item["sort_key"],
            }

    return _RepoScan(candidate, blocked, bool(order), saw_claimed, saw_waiting)


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

    Considers only issues that are ready, free of open blockers, and carrying neither
    the ``archon:in-progress`` nor the ``needs-author`` label, in computed backlog order.
    Omitting *repo* considers every registered repo, merging their backlogs by tier and
    age, in which case runners-up are listed under ``also_eligible``.

    Returns ``issue`` (``None`` when nothing qualifies) with its ``notes`` oldest
    first when one is selected, a ``reason`` of
    ``selected`` / ``all_blocked`` / ``all_waiting`` / ``all_in_progress`` /
    ``none_ready`` / ``empty``, a one-sentence ``explanation``, and ``blocked`` — the
    ready issues passed over because a dependency of theirs is still open, each naming
    its blockers. An unscoped call skips a repo it cannot read and says so in the
    explanation, rather than letting one unreachable repo cost the caller every other
    repo's backlog.
    """
    user_id = _require_user_id()
    if repo is not None:
        repos = [await _resolve_repo(repo)]
    else:
        repos = [normalize_repo(row["repo_name"]) for row in db.list_repos(user_id)]

    now = datetime.now(UTC)
    cache: dict[tuple[str, int], dict[str, Any] | None] = {}
    scans: list[_RepoScan] = []
    unreadable: list[str] = []
    for repo_name in repos:
        try:
            scans.append(await _scan_repo(repo_name, user_id, now, cache))
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
    # Tiers and creation times compare across repos; registration order only breaks a
    # tie between issues of the same tier created at the same moment.
    candidates.sort(key=lambda pair: (pair[1]["sort_key"], pair[0]))
    for _, candidate in candidates:
        del candidate["sort_key"]
    caveat = f" Could not read the backlog of {', '.join(unreadable)}." if unreadable else ""

    if candidates:
        chosen = candidates[0][1]
        explanation = (
            f"{chosen['repo']}#{chosen['number']} is marked interrupt, so it leads the backlog."
            if chosen["interrupt"]
            else f"{chosen['repo']}#{chosen['number']} is the highest-ordered ready issue "
            f"with no open blockers (tier {chosen['tier']})."
        )
        result = {
            "issue": chosen,
            "notes": db.list_notes(user_id, chosen["repo"], issue=chosen["number"]),
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
    elif any(scan.saw_waiting for scan in scans):
        reason = "all_waiting"
        explanation = (
            f"Every ready, unblocked issue not already in progress carries {_NEEDS_AUTHOR_LABEL} "
            "— answer_author on one makes it eligible again."
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


class WaitingOnAuthor(NamedTuple):
    """The questions waiting on the author, and the repos that could not be searched."""

    issues: list[dict[str, Any]]
    unreadable: list[str]


async def waiting_on_author(repos: list[str]) -> WaitingOnAuthor:
    """Return every open ``needs-author`` issue across *repos*, oldest first."""
    issues: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for repo_name in repos:
        try:
            waiting = await github.list_waiting_issues(repo_name, _NEEDS_AUTHOR_LABEL)
        except Exception as exc:
            logger.warning("waiting_on_author: could not search %s error=%r", repo_name, exc)
            unreadable.append(repo_name)
            continue
        issues.extend({"repo": repo_name, **issue} for issue in waiting)
    issues.sort(key=lambda issue: issue["waiting_since"])
    return WaitingOnAuthor(issues, unreadable)


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

    A repository that uses specs also lists its spec changes under
    ``spec_changes``, each as :func:`tools.specs.list_spec_changes` returns it.  A
    spec change records no status, so the ``status`` filter selects it on its
    derived status, and it is counted in ``summary.spec_changes_by_status`` under
    that status rather than in ``by_status`` — ``none`` when no issue names it,
    ``unreadable`` when its issues could not be read.
    Dropped spec changes are left out of the listings like cancelled requirements.
    ``summary.spec_gaps`` maps each such repository to its open spec-gap issues
    counted by capability.  Spec changes are grouped alongside requirement files,
    each group entry carrying ``spec_change`` and ``url`` where a requirement's
    carries ``id``, ``status`` and ``github_issue``.

    ``groups`` answers what shipped, what was dropped and what went quiet without
    a second call.  Every requirement read lands in exactly one of ``dropped``,
    ``awaiting_merge``, ``landed``, ``needs_attention``, ``unreadable``,
    ``never_linked``, ``stalled``, ``diverged`` and ``in_flight``, tested in that
    order, and every group is present even when empty.  ``needs_attention`` holds
    only work that was acted upon and then went wrong, so it stays a worklist:
    ``never_linked`` collects the requirements no issue was ever opened for —
    work not started, not work gone wrong — and ``unreadable`` the ones whose
    linked issues could not be read, where a large group is a GitHub read failure
    rather than a corpus of faults.  ``awaiting_merge`` holds the requirements
    whose file is still on an open Lachesis pull request, oldest PR first, each
    carrying its ``pr`` and its ``kinds`` when a fault is present too.
    ``awaiting_merge_count`` repeats its size next to ``unlinked_count``, which it
    neither contains nor is contained by: a requirement on a PR branch may link
    issues, and the two counts are read off different bases — the whole corpus for
    the groups, the returned listings for ``unlinked_count`` — so they must not be
    summed.  Grouping covers the whole corpus regardless of ``include_inactive``
    — otherwise ``dropped`` would be empty by default — and is narrowed only by
    ``status``.
    A requirement counts as stalled after ``stale_after_days`` of silence unless
    an open blocker is itself still moving, which explains it.

    With a ``status`` filter the group counts sum to the matching units of work
    while ``summary.total`` keeps counting the whole corpus, requirement files and
    spec changes both, as it always has.

    ``waiting_on_author`` lists every open issue labelled ``needs-author`` in the
    repos that were listed, oldest first, apart from ``groups`` because these are
    issues rather than units of work.  Each carries ``repo``, ``number``, ``title``,
    ``url``, ``question`` — the comment posted when it was labelled, else the latest
    comment, ``None`` when there is none — and ``waiting_since``.  The ``status``
    filter does not narrow it.  ``summary.waiting_on_author_count`` gives its size.

    ``summary.incomplete_repos`` names the repos whose open-pull-request fetch
    failed, whose spec changes and spec gaps could not be read, or whose
    questions waiting on the author could not be searched, and is empty
    when every repo was read in full.  While it is non-empty
    every count in ``summary`` is a floor rather than a total — ``total`` and
    ``by_status`` included, since a pull request branch that was not walked
    contributes nothing to them — so a zero there means "none found" only for the
    repos absent from that list.

    ``summary.unavailable_count`` counts the returned requirements and spec
    changes whose issues could not be read, and ``unavailable_repos`` names where they are,
    ``{"repo", "count", "causes"}`` per repo with the distinct causes seen.  It
    is a different failure from ``incomplete_repos``: those requirements were
    found and counted, so only their ``derived`` block is unknown, not the totals.
    Both are counted over the returned listings like ``unlinked_count``, not over
    the whole corpus the groups cover, so a cancelled requirement whose issues
    could not be read is grouped without reaching this count.
    Both are present and empty when nothing failed.

    ``summary.failed_repos`` names the repos that were not read at all, ``{"repo",
    "cause"}`` each, its cause drawn from the same set as ``unavailable_cause`` less
    ``issue_missing``, and is empty when every repo contributed.  It is the widest of
    the three failures: such a repo reached neither ``repos`` nor the grouping, so
    ``unlinked_count`` and every ``groups`` count miss it entirely, while ``total``
    and ``by_status`` may still carry the requirements read before it failed.  A repo
    that was listed and then hit a failure afterwards stays out of this list — its
    listing is intact — and is reported in the logs only.

    ``summary.lower_bound`` is true while either ``incomplete_repos`` or
    ``failed_repos`` is non-empty, saying in one field that the counts are floors.
    """
    from .specs import (
        SPEC_CHANGE_ONLY_STATUSES,
        read_spec_progress,
        spec_change_status,
        uses_specs,
    )

    accepted_statuses = VALID_STATUSES | SPEC_CHANGE_ONLY_STATUSES
    if status is not None and status not in accepted_statuses:
        raise ValueError(_invalid_status_message(status, accepted_statuses))
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
    spec_change_counts: dict[str, int] = {}
    spec_gaps: dict[str, dict[str, int]] = {}
    incomplete_repos: list[str] = []
    failed_repos: list[dict[str, Any]] = []

    for repo_row in repos:
        repo_name = normalize_repo(repo_row["repo_name"])
        listed = False
        try:
            files = await github.list_files(repo_name, "requirements")
            copies = await _read_default_branch(repo_name, files)

            try:
                lachesis_prs = await github.list_lachesis_prs(repo_name)
                for open_pr in lachesis_prs:
                    await _read_pull_request_branch(repo_name, open_pr, copies)
            except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
                # Falls through rather than reaching the outer handler: what this repo's
                # default branch already yielded is real, and every count below it is a
                # floor that incomplete_repos qualifies.
                logger.warning(
                    "get_dashboard: PR-branch pass failed repo=%s error=%r", repo_name, exc
                )
                incomplete_repos.append(repo_name)

            requirements: list[dict[str, Any]] = []
            repo_candidates: list[groups.Candidate] = []
            seen_ids: set[str] = set()
            pending: _Pending = []
            landing_candidates: list[landing.Candidate] = []
            for path, (content, pr_info) in copies.items():
                post = frontmatter.loads(content)
                req_id = _requirement_id(path, post)
                if pr_info is not None and req_id in seen_ids:
                    continue
                seen_ids.add(req_id)
                req_status = canonical_status(post.get("status", ""))
                status_counts[req_status] = status_counts.get(req_status, 0) + 1
                if status is not None and req_status != status:
                    continue
                numbers = linked_issue_numbers(post)
                pr = _pr_field(pr_info)
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
                pending.append((entry, numbers, pr is not None))
                if pr is None:
                    landing_candidates.append(landing.Candidate(path, content, entry))

            await _attach_derived(repo_name, pending)
            repo_entry: dict[str, Any] = {
                "repo": repo_name,
                "requirements": requirements,
                "spec_changes": [],
            }
            aggregated.append(repo_entry)
            listed = True
            all_candidates.extend(repo_candidates)
            # After the append, so a raise here could not cost the repo its listing
            # under this except's misleading log line. A status filter narrows the
            # candidates, which loses nothing: an unconsidered requirement is never
            # claimed, so a later unfiltered read still announces it.
            await landing.notify_landed(repo_name, landing_candidates)
        except Exception as exc:
            logger.warning(
                "get_dashboard: failed to fetch requirements from %s error=%r", repo_name, exc
            )
            if not listed:
                failed_repos.append(
                    {"repo": repo_name, "cause": github.failure_from_exception(exc)}
                )
        if not listed:
            continue

        try:
            if not await uses_specs(repo_name):
                continue
            progress = await read_spec_progress(repo_name)
        except Exception as exc:
            logger.warning(
                "get_dashboard: failed to read spec changes from %s error=%r", repo_name, exc
            )
            if repo_name not in incomplete_repos:
                incomplete_repos.append(repo_name)
            continue
        spec_gaps[repo_name] = progress.spec_gaps
        for entry in (candidate.entry for candidate in progress.landing_candidates):
            spec_status = spec_change_status(entry)
            spec_change_counts[spec_status] = spec_change_counts.get(spec_status, 0) + 1
            if status is not None and spec_status != status:
                continue
            all_candidates.append(
                groups.Candidate(repo_name, entry, entry["derived"]["linked_issues"])
            )
            if status is None and not include_inactive and entry["dropped"]:
                continue
            repo_entry["spec_changes"].append(entry)
        await landing.notify_landed(repo_name, progress.landing_candidates)

    total = sum(status_counts.values()) + sum(spec_change_counts.values())
    unlinked_count = sum(
        1
        for repo_entry in aggregated
        for req in repo_entry["requirements"]
        if not req["derived"]["linked_issues"]
    )
    unavailable: dict[str, list[str]] = {}
    for listing in aggregated:
        for unit in (*listing["requirements"], *listing["spec_changes"]):
            if unit["derived"]["source"] == "unavailable":
                unavailable.setdefault(listing["repo"], []).append(
                    unit["derived"]["unavailable_cause"]
                )
    grouped = groups.group_requirements(
        all_candidates, now=datetime.now(UTC), stale_after_days=stale_after_days
    )
    waiting = await waiting_on_author([listing["repo"] for listing in aggregated])
    incomplete_repos.extend(set(waiting.unreadable) - set(incomplete_repos))
    return {
        "repos": aggregated,
        "waiting_on_author": waiting.issues,
        "groups": grouped,
        "summary": {
            "waiting_on_author_count": len(waiting.issues),
            "total": total,
            "by_status": status_counts,
            "spec_changes_by_status": spec_change_counts,
            "spec_gaps": spec_gaps,
            "unlinked_count": unlinked_count,
            "unavailable_count": sum(len(causes) for causes in unavailable.values()),
            "unavailable_repos": [
                {"repo": repo_name, "count": len(causes), "causes": sorted(set(causes))}
                for repo_name, causes in sorted(unavailable.items())
            ],
            "awaiting_merge_count": len(grouped["awaiting_merge"]),
            "groups": {name: len(grouped[name]) for name in groups.GROUP_NAMES},
            "incomplete_repos": sorted(incomplete_repos),
            "failed_repos": sorted(failed_repos, key=lambda entry: entry["repo"]),
            "lower_bound": bool(incomplete_repos or failed_repos),
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
    mergeable, mergeable_state, head_ref, base_ref, draft and labels, or ``{"found": False}``
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


async def get_comments(repo: str, number: int) -> list[dict[str, Any]]:
    """Return the comments on an issue or pull request, oldest first.

    Each comment carries ``author``, ``created_at``, ``body`` and ``url``.
    """
    repo = await _resolve_repo(repo, require_registered=True)
    comments = await github.list_issue_comments(repo, number)
    if comments is None:
        raise FileNotFoundError(f"Issue or pull request #{number} not found in {repo!r}")
    return comments


async def post_comment(repo: str, number: int, body: str) -> dict[str, Any]:
    """Post *body* as a comment on an issue or pull request and return the comment's URL."""
    if not body.strip():
        raise ValueError("body must not be empty")
    repo = await _resolve_repo(repo, require_registered=True)
    url = await github.create_issue_comment(repo, number, body)
    if url is None:
        raise FileNotFoundError(f"Issue or pull request #{number} not found in {repo!r}")
    return {"url": url}


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
