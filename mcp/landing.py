"""Decide which requirements have just landed and announce them once.

Evaluated as a side effect of the requirement read paths rather than from an
event source: a read is what brings the repo's issue and pull request facts into
memory, and Archon — which merges the work — is a heavy caller of those reads.
Nothing is consumed by a read that does not happen, so a quiet period only
delays a notification, never cancels it.

Like :mod:`notify`, nothing here raises into its caller: the read the author
asked for must not fail because a notification could not be worked out.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any, Literal, NamedTuple

import db
import github
import notify
from auth import current_db_user_id
from status import INACTIVE_STATUSES, canonical_status

logger = logging.getLogger(__name__)

Verdict = Literal["merged", "deployed", "failed"]

_SUMMARY_MAX_CHARS = 200
_EMPHASIS = re.compile(r"[*_]+")
_BOLD_ONLY = re.compile(r"^\*\*[^*]+\*\*$")


class Candidate(NamedTuple):
    """One requirement read off the default branch, with the file it was read from."""

    path: str
    content: str
    entry: dict[str, Any]


class Notification(NamedTuple):
    """The composed announcement for one landed requirement."""

    title: str
    body: str
    click: str
    tags: list[str]
    priority: notify.Priority | None


def _extract_section(body: str, heading: str) -> str:
    """Return the text content of a '## {heading}' section from a markdown body."""
    normalized = (body or "").replace("\r\n", "\n")
    pattern = rf"(?:^|\n)## {re.escape(heading)}\s*\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, normalized, re.DOTALL)
    return m.group(1).strip() if m else ""


def summarize_what(content: str, title: str) -> str:
    """Return a one-line description of the capability, falling back to *title*.

    The notification exists to prompt the author to go and use the thing, so the
    first line of prose in ``## What`` is the summary — not a bold lead-in like
    ``**The trigger**``, which labels a subsection rather than describing it.
    """
    for raw in _extract_section(content, "What").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or _BOLD_ONLY.match(line):
            continue
        line = _EMPHASIS.sub("", line)
        line = " ".join(line.split())
        if not line:
            continue
        if len(line) <= _SUMMARY_MAX_CHARS:
            return line
        return line[:_SUMMARY_MAX_CHARS].rsplit(" ", 1)[0] + "..."
    return title


def _as_datetime(value: Any) -> datetime | None:
    """Read *value* as an aware datetime, or ``None`` when it is not one.

    The epoch arrives from Postgres as a ``datetime`` and GitHub's stamps arrive
    as strings, so both shapes reach the comparison.  A naive value is assumed
    UTC, as :func:`groups.days_since` already assumes for GitHub stamps.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _landed_after(candidate: Candidate, epoch: datetime) -> bool:
    """Whether this requirement finished after the account asked to hear about it.

    An inactive requirement is never announced however its issues resolved: a
    cancelled requirement whose work happened to merge is not a capability the
    author is being invited to go and use, and announcing it would spend the one
    notification it will ever be owed.
    """
    if canonical_status(candidate.entry.get("status", "")) in INACTIVE_STATUSES:
        return False
    derived = candidate.entry.get("derived") or {}
    if derived.get("status") != "done":
        return False
    landed_at = _as_datetime(derived.get("landed_at"))
    return landed_at is not None and landed_at > epoch


async def _verdict(
    repo: str, signal: str, candidate: Candidate
) -> tuple[Verdict, str | None] | None:
    """Return ``(state, url)`` for a landed requirement, or ``None`` to decide later.

    ``None`` leaves the notification owed: no claim is taken, so a deployment
    that has not finished is reconsidered on the next read.
    """
    if signal != "deployment":
        return "merged", None

    commit = (candidate.entry.get("derived") or {}).get("landed_commit")
    if not commit:
        logger.info(
            "landing: %s %s landed without a merge commit; announcing the merge",
            repo,
            candidate.entry.get("id"),
        )
        return "merged", None

    deployment = await github.get_production_deployment(repo, commit)
    if deployment is None or deployment["state"] == "pending":
        return None
    if deployment["state"] == "failure":
        return "failed", deployment["url"]
    return "deployed", deployment["url"]


def _message(repo: str, candidate: Candidate, state: Verdict, url: str | None) -> Notification:
    """Compose the announcement for one verdict.

    A failed deployment is the only verdict that asks the author to do something
    now, so it alone is raised above the ntfy default priority: a landing they
    are merely invited to go and use must not arrive with the same urgency.
    """
    req_title = str(candidate.entry.get("title", ""))
    summary = summarize_what(candidate.content, req_title)
    if state == "failed":
        return Notification(
            f"Failed to deploy: {req_title} ({repo})",
            f"{summary}\n\nThe deployment carrying this requirement failed.",
            url or f"https://github.com/{repo}/deployments",
            ["rotating_light"],
            "high",
        )
    closing = (
        "Deployed to production."
        if state == "deployed"
        else "Merged to the default branch — deployment is not reported for this repo."
    )
    return Notification(
        f"{req_title} ({repo})",
        f"{summary}\n\n{closing}",
        f"https://github.com/{repo}/blob/HEAD/{candidate.path}",
        ["rocket"],
        None,
    )


async def notify_landed(repo: str, candidates: list[Candidate]) -> None:
    """Announce every candidate that has just landed. Never raises."""
    try:
        user_id = current_db_user_id.get()
        if not user_id:
            return

        config = db.get_ntfy_config(user_id)
        if config is None or not config["topic"]:
            return
        epoch = _as_datetime(config.get("created_at"))
        if epoch is None:
            logger.warning(
                "landing: ntfy config for user=%s carries no usable created_at; "
                "treating every landing as history",
                user_id,
            )
            return

        landed = [c for c in candidates if _landed_after(c, epoch)]
        if not landed:
            return

        already = db.notified_requirement_ids(user_id, repo)
        landed = [c for c in landed if str(c.entry.get("id")) not in already]
        if not landed:
            return

        signal = db.get_landing_signal(user_id, repo)
        for candidate in landed:
            verdict = await _verdict(repo, signal, candidate)
            if verdict is None:
                continue
            state, url = verdict
            note = _message(repo, candidate, state, url)
            await notify.notify_requirement(
                user_id,
                repo=repo,
                requirement_id=str(candidate.entry.get("id")),
                title=note.title,
                body=note.body,
                click=note.click,
                tags=note.tags,
                priority=note.priority,
            )
    except Exception:
        logger.warning("landing: could not evaluate landings for %s", repo, exc_info=True)
