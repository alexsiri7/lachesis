"""Lachesis MCP tool implementations — audits: every run recorded, due when the code or the
auditor changes, started one at a time per repository, findings filed and never fixed."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, get_args

import httpx

import db
import github
from spec_reference import SpecReference, append_reference

from .questions import _tell_author
from .repos import _require_user_id, normalize_repo
from .requirements import (
    _NEEDS_AUTHOR_COLOR,
    _NEEDS_AUTHOR_LABEL,
    _SPEC_GAP_COLOR,
    SPEC_GAP_LABEL,
    _resolve_repo,
)
from .routing import route_run
from .specs import ACCEPTANCE_TESTS_COLOR, ACCEPTANCE_TESTS_LABEL, SPECS_DIR

logger = logging.getLogger(__name__)

AUDIT_LABEL = "audit"
_AUDIT_COLOR = "5319e7"

# A security audit falls due this long after its last successful run, changes or not.
SECURITY_INTERVAL = timedelta(days=60)

# Audits that cover one capability at a time in a repository that uses specs.
_PER_CAPABILITY: tuple[db.AuditKind, ...] = ("tests", "requirements")

Finding = Literal["weak_test", "unmet_requirement", "undescribed_behaviour", "ambiguous_wording"]

_CHECKS: dict[str, list[str]] = {
    "spec": [
        "Behaviour in the code that no scenario describes.",
        "Scenarios whose acceptance test does not genuinely check them.",
        "Requirements the code does not meet.",
        "Wording in the spec open to more than one reading.",
    ],
    "architecture": [
        "Structure that makes the code hard to change: tangled modules, duplication, "
        "layers that leak.",
        "Designs that will not hold as usage grows.",
    ],
    "security": [
        "Vulnerabilities in the code: injection, broken access control, secrets, unsafe defaults.",
        "Dependencies with known vulnerabilities or no longer maintained.",
    ],
    "clean-code": [
        "Names that reveal intent: no abbreviations, puns or names that say less than the "
        "thing does.",
        "Small functions that do one thing, at one level of abstraction.",
        "Few arguments, and no flag arguments.",
        "No side effects hidden behind a name that does not mention them.",
        "No duplication.",
        "Comments that explain why, not restate what the code says.",
        "Classes and modules with a single responsibility.",
        "Dependencies that point inward: policy never depends on detail.",
        "Tests that are fast, independent, repeatable, self-validating and as clean as the code.",
    ],
    "tests": [
        "Tests that pass without checking what they claim.",
        "Behaviour with no test at all.",
    ],
    "requirements": [
        "Requirements the code does not meet.",
        "Behaviour no requirement describes.",
    ],
}

NORTH_STAR_PATH = "docs/north-star.md"

_NORTH_STAR_GAPS = (
    "Take each story in turn and find what in the specs, the backlog or the factory's "
    "behaviour stands between the system and that story. File each gap:\n"
    "\n"
    "- A gap the specs already promise (the behaviour is specified but not delivered): a "
    "spec-gap issue, with file_audit_finding and finding unmet_requirement.\n"
    "- A gap the specs do not cover: a question to the author, with ask_question kind "
    "improvement, proposing the spec change that would close it and naming the story it "
    "serves."
)

_NAME_THE_STANDARD = (
    "These are Robert C. Martin's clean code standards. Each finding must name the standard "
    "it breaks and where (file and function)."
)

_FILING = (
    "File every finding with file_audit_finding naming this issue; change no code, test or "
    "spec. Close this issue once every finding is filed, listing them, and report the run "
    "with report_audit_run."
)


async def _label(repo: str, name: str, color: str) -> None:
    """Give label *name* its colour, best effort: GitHub creates a missing label anyway when
    an issue is created with it, so failing here must not stop the filing."""
    try:
        await github.ensure_label(repo, name, color)
    except httpx.HTTPError as exc:
        logger.warning("audits: could not ensure label %s in %s error=%r", name, repo, exc)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def report_audit_run(
    repo: str,
    kind: db.AuditKind,
    outcome: db.AuditOutcome,
    finished_at: str,
    model: str,
    issues: list[int] | None = None,
    capability: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> dict[str, Any]:
    """Record one audit run of *kind* in *repo*: when it finished, its outcome, the model
    that ran it, the issues it filed and the tokens it used."""
    if not model.strip():
        raise ValueError("model must name the model that ran the audit")
    if tokens_in < 0 or tokens_out < 0:
        raise ValueError("tokens must not be negative")
    finished = _parse_time(finished_at)
    if finished > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError(f"finished_at must not be in the future, got {finished_at!r}")
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    return db.record_audit_run(
        user_id,
        repo,
        kind,
        capability,
        model.strip(),
        finished,
        outcome,
        sorted(set(issues or [])),
        tokens_in,
        tokens_out,
    )


def _merged_since(listing: list[github.IssueSummary], since: datetime | None) -> int:
    """Pull requests merged into *listing*'s repository after *since* (all when None)."""
    return sum(
        1
        for item in listing
        if item["is_pull_request"]
        and item["merged"]
        and item["closed_at"]
        and (since is None or _parse_time(item["closed_at"]) > since)
    )


def _last(runs: list[dict[str, Any]], *, successful: bool = False) -> dict[str, Any] | None:
    chosen = [run for run in runs if not successful or run["outcome"] != "failed"]
    return chosen[-1] if chosen else None


def _due(
    kind: str,
    runs: list[dict[str, Any]],
    listing: list[github.IssueSummary],
    model: str,
    now: datetime,
) -> tuple[bool, int]:
    """Whether an audit of *kind* with these *runs* (oldest first) is due, and the pull
    requests merged since its last run. A capability never audited by a per-capability
    kind makes it due too; the caller adds that."""
    last = _last(runs)
    success = _last(runs, successful=True)
    merged = _merged_since(listing, success["finished_at"] if success else None)
    if last is None or last["outcome"] == "failed" or success is None:
        return True, merged
    if merged or success["model"] != model:
        return True, merged
    if kind == "security" and now - success["finished_at"] >= SECURITY_INTERVAL:
        return True, merged
    return False, merged


async def _capabilities(repo: str) -> list[str]:
    entries = await github.list_files(repo, SPECS_DIR)
    return sorted(e["name"] for e in entries or [] if e.get("type") == "dir")


def _next_capability(capabilities: list[str], runs: list[dict[str, Any]]) -> str | None:
    """The capability audited least recently by these *runs*, one never audited first."""
    if not capabilities:
        return None
    last: dict[str, datetime] = {}
    for run in runs:
        if run["capability"] in capabilities and run["outcome"] != "failed":
            last[run["capability"]] = run["finished_at"]
    epoch = datetime.min.replace(tzinfo=UTC)
    return min(capabilities, key=lambda name: (last.get(name, epoch), name))


def _audited(capability: str, runs: list[dict[str, Any]]) -> bool:
    return any(run["capability"] == capability and run["outcome"] != "failed" for run in runs)


def _run_view(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "finished_at": run["finished_at"].isoformat(),
        "outcome": run["outcome"],
        "model": run["model"],
        "issues": run["issues"],
        "capability": run["capability"],
        "tokens": run["tokens_in"] + run["tokens_out"],
    }


async def audit_history(user_id: str) -> dict[str, Any]:
    """Every registered repository's audits, each kind's last run and whether it is due,
    and the due audits in order: most pull requests merged since their last run first,
    then the oldest last run."""
    now = datetime.now(UTC)
    model = (await route_run("audit"))["model"]
    runs = db.list_audit_runs(user_id)
    repos = [normalize_repo(row["repo_name"]) for row in db.list_repos(user_id)]
    listings: list[dict[str, Any]] = []
    due: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    unreadable: list[str] = []
    for repo in repos:
        try:
            listing = await github.list_all_issues(repo)
            capabilities = await _capabilities(repo)
        except Exception as exc:
            logger.warning("audit_history: could not read %s error=%r", repo, exc)
            unreadable.append(repo)
            continue
        has_north_star = await _north_star(repo) is not None
        audits = []
        for kind in get_args(db.AuditKind):
            mine = [r for r in runs if normalize_repo(r["repo_name"]) == repo and r["kind"] == kind]
            is_due, merged = _due(kind, mine, listing, model, now)
            capability = _next_capability(capabilities, mine) if kind in _PER_CAPABILITY else None
            if capability is not None and not _audited(capability, mine):
                is_due = True
            if kind == "north-star" and not has_north_star:
                is_due = False
            last = _last(mine)
            audits.append(
                {
                    "kind": kind,
                    "last_run": _run_view(last),
                    "due": is_due,
                    "merged_since": merged,
                    "capability": capability,
                }
            )
            if is_due:
                oldest = last["finished_at"] if last else datetime.min.replace(tzinfo=UTC)
                due.append(
                    (
                        (-merged, oldest, repo, kind),
                        {"repo": repo, "kind": kind, "capability": capability},
                    )
                )
        listings.append({"repo": repo, "audits": audits})
    due.sort(key=lambda entry: entry[0])
    return {
        "model": model,
        "repos": listings,
        "due": [entry for _, entry in due],
        "unreadable": unreadable,
    }


async def read_audit_history() -> dict[str, Any]:
    """Return every registered repository's audit history and the due audits in order."""
    return await audit_history(_require_user_id())


def _open_audit(listing: list[github.IssueSummary]) -> int | None:
    for item in listing:
        if (
            not item["is_pull_request"]
            and item["state"] == "open"
            and AUDIT_LABEL in item["labels"]
        ):
            return int(item["number"])
    return None


async def _north_star(repo: str) -> str | None:
    """The repository's north star, or ``None`` when it has none or it cannot be read."""
    try:
        content, _ = await github.get_file(repo, NORTH_STAR_PATH)
    except FileNotFoundError:
        return None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("audits: could not read the north star of %s error=%r", repo, exc)
        return None
    return content


def north_star_stories(north_star: str) -> list[str]:
    """The story titles: each ``### `` heading under the ``## Stories`` section."""
    stories: list[str] = []
    in_stories = False
    for line in north_star.splitlines():
        if line.startswith("## "):
            in_stories = line[3:].strip().lower() == "stories"
        elif in_stories and line.startswith("### "):
            stories.append(line[4:].strip())
    return stories


def _north_star_body(repo: str, north_star: str) -> str:
    stories = north_star_stories(north_star)
    lines = [f"A north-star audit of {repo} against {NORTH_STAR_PATH}. Its stories:", ""]
    lines += [f"- {story}" for story in stories] or ["- (no stories found under ## Stories)"]
    return "\n".join([*lines, "", _NORTH_STAR_GAPS, "", _FILING])


def _audit_body(kind: str, capability: str | None, repo: str) -> str:
    subject = f"the {capability} capability" if capability else repo
    checks = _CHECKS["spec"] if capability else _CHECKS[kind]
    lines = [f"A {kind} audit of {subject}. Check:", ""]
    lines += [f"- {check}" for check in checks]
    if kind == "clean-code":
        lines += ["", _NAME_THE_STANDARD]
    return "\n".join([*lines, "", _FILING])


async def start_audit(repo: str, kind: db.AuditKind) -> dict[str, Any]:
    """Open the audit issue for the next due audit of *kind* in *repo*.

    Refused while an earlier audit issue in *repo* is open, and when no audit of *kind*
    is due there. A north-star audit is due only in a repository with
    ``docs/north-star.md``; its issue lists the north star's stories.
    """
    if kind not in get_args(db.AuditKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(db.AuditKind))}, got {kind!r}")
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    listing = await github.list_all_issues(repo)
    earlier = _open_audit(listing)
    if earlier is not None:
        raise ValueError(
            f"Audit issue #{earlier} in {repo} is still open: finish it before starting another."
        )
    history = await audit_history(user_id)
    mine = next((entry for entry in history["repos"] if entry["repo"] == repo), None)
    if mine is None:
        raise RuntimeError(f"Could not read the audit history of {repo}")
    audit = next(entry for entry in mine["audits"] if entry["kind"] == kind)
    if not audit["due"]:
        raise ValueError(f"No {kind} audit is due in {repo}: nothing has changed since the last.")
    capability = audit["capability"]
    subject = capability or repo
    if kind == "north-star":
        north_star = await _north_star(repo)
        if north_star is None:
            raise ValueError(f"{repo} has no north star ({NORTH_STAR_PATH}) to audit against.")
        body = _north_star_body(repo, north_star)
    else:
        body = _audit_body(kind, capability, repo)
    await _label(repo, AUDIT_LABEL, _AUDIT_COLOR)
    issue = await github.create_issue(repo, f"Audit ({kind}): {subject}", body, [AUDIT_LABEL])
    return {
        "repo": repo,
        "kind": kind,
        "capability": capability,
        "model": history["model"],
        "issue": issue["number"],
        "url": issue.get("url"),
    }


_QUESTIONS: dict[str, str] = {
    "undescribed_behaviour": (
        "The code does something no scenario describes: {title}. Keep it and add it to the "
        "spec, or remove it?"
    ),
    "ambiguous_wording": (
        "The spec's wording can be read more than one way: {title}. Which reading is intended?"
    ),
}


async def file_audit_finding(
    repo: str,
    audit_issue: int,
    finding: Finding,
    capability: str,
    title: str,
    body: str,
) -> dict[str, Any]:
    """File one finding of audit issue *audit_issue* as a spec-gap issue.

    A weak test is also labelled acceptance-tests. Undescribed behaviour and ambiguous
    wording wait on the author with a question framed as a decision.
    """
    if finding not in get_args(Finding):
        raise ValueError(f"finding must be one of {', '.join(get_args(Finding))}, got {finding!r}")
    if not title.strip():
        raise ValueError("a finding needs a title")
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    labels = [SPEC_GAP_LABEL]
    await _label(repo, SPEC_GAP_LABEL, _SPEC_GAP_COLOR)
    if finding == "weak_test":
        labels.append(ACCEPTANCE_TESTS_LABEL)
        await _label(repo, ACCEPTANCE_TESTS_LABEL, ACCEPTANCE_TESTS_COLOR)
    question = _QUESTIONS.get(finding, "").format(title=title.strip())
    text = f"{body.strip()}\n\nFound by audit #{audit_issue}."
    if question:
        # The issue itself carries the question, so it never waits without one.
        text += f"\n\n**Question for the author:** {question}"
        labels.append(_NEEDS_AUTHOR_LABEL)
        await _label(repo, _NEEDS_AUTHOR_LABEL, _NEEDS_AUTHOR_COLOR)
    issue = await github.create_issue(
        repo,
        title.strip(),
        append_reference(text, SpecReference(None, (capability,))),
        labels,
    )
    if question:
        number = int(issue["number"])
        db.record_question(
            user_id,
            repo,
            question,
            "clarification",
            "audit",
            capability=capability,
            issue=number,
            blocks=[number],
        )
        await _tell_author(user_id, repo, question, "clarification", [number], number)
    return {"repo": repo, "finding": finding, "issue": issue["number"], "url": issue.get("url")}
