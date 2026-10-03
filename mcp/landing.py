"""Decide which units of work have just landed, and announce each once.

A unit of work is a requirement file, or a spec change in a repository that uses
specs.  Evaluated as a side effect of the read paths that list them rather than
from an event source: a read is what brings the repo's issue and pull request facts into
memory, and Archon — which merges the work — is a heavy caller of those reads.
Nothing is consumed by a read that does not happen, so a quiet period only
delays a notification, never cancels it.

Like :mod:`notify`, nothing here raises into its caller: the read the author
asked for must not fail because a notification could not be worked out.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
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
_FIRST_SENTENCE = re.compile(r".+?[.!?](?=\s|$)")


class Candidate(NamedTuple):
    """One requirement read off the default branch, with the file it was read from."""

    path: str
    content: str
    entry: dict[str, Any]

    @property
    def kind(self) -> str:
        return "requirement"

    @property
    def key(self) -> str:
        return str(self.entry.get("id"))

    @property
    def withdrawn(self) -> bool:
        return canonical_status(self.entry.get("status", "")) in INACTIVE_STATUSES

    def summary(self) -> str:
        return summarize_what(self.content, str(self.entry.get("title", "")))

    def link(self, repo: str) -> str:
        return f"https://github.com/{repo}/blob/HEAD/{self.path}"


class SpecChangeCandidate(NamedTuple):
    """One spec change, with the description of the pull request that proposes it."""

    description: str
    entry: dict[str, Any]

    @property
    def kind(self) -> str:
        return "spec change"

    @property
    def key(self) -> str:
        # Shares the notification ledger with requirement ids, which are plain
        # numbers such as "026", so a spec change's number is kept apart from them.
        return f"spec:{self.entry['spec_change']}"

    @property
    def withdrawn(self) -> bool:
        return bool(self.entry.get("dropped"))

    def summary(self) -> str:
        return summarize_description(self.description, str(self.entry.get("title", "")))

    def link(self, repo: str) -> str:
        return str(self.entry["url"])


Landable = Candidate | SpecChangeCandidate


class Notification(NamedTuple):
    """The composed announcement for one landed unit of work."""

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
        if _is_boilerplate(line):
            continue
        line = _plain(line)
        if line:
            return _clip(line)
    return title


def summarize_description(description: str, title: str) -> str:
    """Return the first sentence of a pull request description, falling back to *title*.

    The sentence is read from the first paragraph of prose, so a heading or a bold
    lead-in above it is skipped, as in :func:`summarize_what`.
    """
    paragraph: list[str] = []
    for raw in (description or "").replace("\r\n", "\n").splitlines():
        line = raw.strip()
        if _is_boilerplate(line):
            if paragraph:
                break
            continue
        paragraph.append(line)
    text = _plain(" ".join(paragraph))
    if not text:
        return title
    sentence = _FIRST_SENTENCE.match(text)
    return _clip(sentence.group(0) if sentence else text)


def _is_boilerplate(line: str) -> bool:
    return not line or line.startswith("#") or bool(_BOLD_ONLY.match(line))


def _plain(line: str) -> str:
    return " ".join(_EMPHASIS.sub("", line).split())


def _clip(line: str, limit: int = _SUMMARY_MAX_CHARS) -> str:
    if len(line) <= limit:
        return line
    return line[:limit].rsplit(" ", 1)[0] + "..."


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


def _landed_after(candidate: Landable, epoch: datetime) -> bool:
    """Whether this unit of work finished after the account asked to hear about it.

    A withdrawn unit — a cancelled or superseded requirement, a dropped spec
    change — is never announced however its issues resolved: work that happened
    to merge is not a capability the author is being invited to go and use, and
    announcing it would spend the one notification it will ever be owed.
    """
    if candidate.withdrawn:
        return False
    derived = candidate.entry.get("derived") or {}
    if derived.get("status") != "done":
        return False
    landed_at = _as_datetime(derived.get("landed_at"))
    return landed_at is not None and landed_at > epoch


async def _verdict(
    repo: str, signal: str, candidate: Landable
) -> tuple[Verdict, str | None] | None:
    """Return ``(state, url)`` for a landed unit of work, or ``None`` to decide later.

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
            candidate.key,
        )
        return "merged", None

    deployment = await github.get_production_deployment(repo, commit)
    if deployment is None or deployment["state"] == "pending":
        return None
    if deployment["state"] == "failure":
        return "failed", deployment["url"]
    return "deployed", deployment["url"]


def _message(repo: str, candidate: Landable, state: Verdict, url: str | None) -> Notification:
    """Compose the announcement for one verdict.

    A failed deployment is the only verdict that asks the author to do something
    now, so it alone is raised above the ntfy default priority: a landing they
    are merely invited to go and use must not arrive with the same urgency.
    """
    title = str(candidate.entry.get("title", ""))
    summary = candidate.summary()
    if state == "failed":
        return Notification(
            f"Failed to deploy: {title} ({repo})",
            f"{summary}\n\nThe deployment carrying this {candidate.kind} failed.",
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
        f"{title} ({repo})",
        f"{summary}\n\n{closing}",
        candidate.link(repo),
        ["rocket"],
        None,
    )


async def notify_landed(repo: str, candidates: Sequence[Landable]) -> None:
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
        landed = [c for c in landed if c.key not in already]
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
                requirement_id=candidate.key,
                title=note.title,
                body=note.body,
                click=note.click,
                tags=note.tags,
                priority=note.priority,
            )
    except Exception:
        logger.warning("landing: could not evaluate landings for %s", repo, exc_info=True)
