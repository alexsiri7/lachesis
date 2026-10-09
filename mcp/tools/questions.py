"""Lachesis MCP tool implementations — asking the author questions."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import db
import github
import notify

from .repos import _require_user_id
from .requirements import _NEEDS_AUTHOR_COLOR, _NEEDS_AUTHOR_LABEL, _resolve_repo

logger = logging.getLogger(__name__)

# Only these kinds may settle by default: the others decide what a project should be.
DEFAULTABLE_KINDS: tuple[db.QuestionKind, ...] = ("clarification", "improvement")
DEFAULT_WAIT_DAYS = 3.0
DEFAULT_ANSWERER = "default"

# ntfy turns a message over 4096 bytes into an attachment; the full question
# stays in its record and on any issue it names.
_QUESTION_MAX_CHARS = 1000


def _clip(question: str) -> str:
    if len(question) <= _QUESTION_MAX_CHARS:
        return question
    return question[:_QUESTION_MAX_CHARS].rsplit(" ", 1)[0] + "..."


def _comment_body(question: str, context: str, options: list[str]) -> str:
    parts = [question]
    if context:
        parts.append(context)
    if options:
        parts.append("Options:\n" + "\n".join(f"- {option}" for option in options))
    return "\n\n".join(parts)


async def _tell_author(
    user_id: str,
    repo: str,
    question: str,
    kind: db.QuestionKind,
    blocked: list[int],
    issue: int | None,
) -> None:
    title = f"{kind.replace('_', ' ').capitalize()} question on {repo}"
    if blocked:
        title += " blocking " + ", ".join(f"#{number}" for number in blocked)
    linked = blocked[0] if blocked else issue
    await notify.send(
        user_id,
        title=title,
        body=_clip(question),
        click=f"https://github.com/{repo}/issues/{linked}" if linked is not None else None,
        tags=["question"],
    )


async def ask_question(
    repo: str,
    question: str,
    kind: db.QuestionKind,
    asker: str,
    blocks: list[int] | None = None,
    context: str = "",
    options: list[str] | None = None,
    capability: str | None = None,
    spec_change: int | None = None,
    milestone: str | None = None,
    issue: int | None = None,
    default: str | None = None,
    wait_days: float = DEFAULT_WAIT_DAYS,
) -> dict[str, Any]:
    """Record a question for the author and mark every issue it *blocks* as waiting.

    A clarification or improvement question may carry a *default* answer, taken
    when *wait_days* pass with no answer (see ``settle_expired_defaults``). Every
    other kind decides what a project should be and must wait for the author, so
    a default for it is refused.

    Each blocked issue gets the question as a comment, then the question is
    recorded, then the author is sent a push (best effort, never raises), then
    each blocked issue is labelled ``needs-author``. That order keeps an issue
    from ever being labelled without both its question comment and its record;
    a failure part-way is raised and leaves what already landed.

    A ``budget`` question blocks nothing, so work continues within the existing
    cap: it is refused with *blocks*, and its *context* must say what the extra
    spend would deliver.

    Returns ``{"id", "comments": [{"issue", "url"}, ...]}``.
    """
    question = question.strip()
    asker = asker.strip()
    context = context.strip()
    options = [option.strip() for option in options or [] if option.strip()]
    blocked = list(dict.fromkeys(blocks or []))
    if not question:
        raise ValueError("question must not be empty")
    if not asker:
        raise ValueError("asker must not be empty")
    if kind not in get_args(db.QuestionKind):
        raise ValueError(
            f"kind must be one of {', '.join(get_args(db.QuestionKind))}, got {kind!r}"
        )
    if kind == "budget" and blocked:
        raise ValueError("a budget question blocks no issue; name the work it delivers in context")
    if kind == "budget" and not context:
        raise ValueError("a budget question's context must say what the extra spend would deliver")
    if default is not None:
        default = default.strip()
        if not default:
            raise ValueError("default must not be empty; omit it to wait for the author")
        if kind not in DEFAULTABLE_KINDS:
            raise ValueError(
                f"a {kind} question cannot carry a default: it decides what the project "
                "should be and waits for the author. Only clarification and improvement "
                "questions settle by default."
            )
        if not wait_days > 0:
            raise ValueError(f"wait_days must be positive, got {wait_days!r}")
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)

    body = _comment_body(question, context, options)
    if default is not None:
        body += f"\n\nUnless answered within {wait_days:g} days, the default is taken: {default}"
    comments: list[dict[str, Any]] = []
    for number in blocked:
        url = await github.create_issue_comment(repo, number, body)
        if url is None:
            raise FileNotFoundError(f"Issue #{number} not found in {repo!r}")
        comments.append({"issue": number, "url": url})

    row = db.record_question(
        user_id,
        repo,
        question,
        kind,
        asker,
        context=context,
        options=options,
        capability=capability,
        spec_change=spec_change,
        milestone=milestone,
        issue=issue,
        blocks=blocked,
        default_answer=default,
        wait=timedelta(days=wait_days) if default is not None else None,
    )

    await _tell_author(user_id, repo, question, kind, blocked, issue)

    if blocked:
        await github.ensure_label(repo, _NEEDS_AUTHOR_LABEL, _NEEDS_AUTHOR_COLOR)
        for number in blocked:
            await github.add_labels(repo, number, [_NEEDS_AUTHOR_LABEL])
    return {"id": row["id"], "comments": comments}


async def ask_author(
    repo: str,
    number: int,
    question: str,
    asker: str = "agent",
    default: str | None = None,
    wait_days: float = DEFAULT_WAIT_DAYS,
) -> dict[str, Any]:
    """Ask a clarification question blocking issue *number*; see ``ask_question``.

    Returns ``{"url"}`` of the question comment.
    """
    result = await ask_question(
        repo,
        question,
        "clarification",
        asker,
        blocks=[number],
        issue=number,
        default=default,
        wait_days=wait_days,
    )
    return {"url": result["comments"][0]["url"]}


async def answer_question(
    repo: str,
    id: int,
    answer: str,
    answerer: str,
    outcome: db.OutcomeKind = "none",
) -> dict[str, Any]:
    """Answer open question *id*, posting *answer* on every issue it blocks.

    A question settled by its default may be answered again: the new answer
    replaces the default.

    The answer is posted on each blocked issue, then recorded with its answerer,
    time and outcome, then ``needs-author`` is removed from each blocked issue no
    other open question still blocks. That order keeps an issue from ever losing
    the label without the answer on it; a failure part-way is raised and leaves
    what already landed.

    Returns ``{"id", "outcome", "comments": [{"issue", "url"}, ...], "released"}``.
    """
    answer = answer.strip()
    answerer = answerer.strip()
    if not answer:
        raise ValueError("answer must not be empty")
    if not answerer:
        raise ValueError("answerer must not be empty")
    if outcome not in get_args(db.OutcomeKind):
        raise ValueError(
            f"outcome must be one of {', '.join(get_args(db.OutcomeKind))}, got {outcome!r}"
        )
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    question = db.get_question(user_id, repo, id)
    if question is None:
        raise FileNotFoundError(f"Question {id} not found in {repo!r}")
    if question["answered_at"] is not None and question["outcome"] != db.DEFAULTED:
        raise ValueError(f"Question {id} in {repo!r} is already answered")

    comments: list[dict[str, Any]] = []
    for number in question["blocks"]:
        url = await github.create_issue_comment(repo, number, answer)
        if url is None:
            raise FileNotFoundError(f"Issue #{number} not found in {repo!r}")
        comments.append({"issue": number, "url": url})

    answered = db.answer_question(user_id, repo, id, answer, answerer, outcome)

    for number in answered["released"]:
        await github.remove_label(repo, number, _NEEDS_AUTHOR_LABEL)
    return {
        "id": id,
        "outcome": outcome,
        "comments": comments,
        "released": answered["released"],
    }


def _default_note(default: str) -> str:
    return f"No answer came within the wait, so the default was taken: {default}"


async def settle_expired_defaults(user_id: str) -> list[dict[str, Any]]:
    """Answer each open question whose wait has passed with its default.

    For each, the default is posted on every issue it blocks as a note that the
    default was taken, then recorded as the answer with answerer ``default`` and
    outcome ``defaulted``, then ``needs-author`` is removed from each issue no other
    open question still blocks — the same order as ``answer_question``. Called on
    every read of the open questions, so it never raises: a question that fails to
    settle is logged and left for the next read.

    Returns the questions settled, each with ``released``.
    """
    try:
        expired = db.list_expired_defaults(user_id)
    except Exception:
        logger.warning("settle_expired_defaults: could not read the questions", exc_info=True)
        return []
    settled: list[dict[str, Any]] = []
    for question in expired:
        repo = question["repo_name"]
        default = question["default_answer"]
        try:
            for number in question["blocks"]:
                await github.create_issue_comment(repo, number, _default_note(default))
            answered = db.answer_question(
                user_id, repo, question["id"], default, DEFAULT_ANSWERER, db.DEFAULTED
            )
            for number in answered["released"]:
                await github.remove_label(repo, number, _NEEDS_AUTHOR_LABEL)
        except Exception:
            logger.warning(
                "settle_expired_defaults: question %s in %s did not settle",
                question["id"],
                repo,
                exc_info=True,
            )
            continue
        settled.append(answered)
    return settled


async def list_open_questions() -> list[dict[str, Any]]:
    """Return the open questions across every registered repository, oldest first.

    Questions whose wait for a default has passed are settled first, so they are
    not listed. Each is its record — kind, what it is about, the issues it
    ``blocks``, any ``default_answer`` and ``default_after`` — with
    ``waiting_hours``: whole hours since it was asked.
    """
    user_id = _require_user_id()
    await settle_expired_defaults(user_id)
    now = datetime.now(UTC)
    return [
        {**row, "waiting_hours": int((now - row["created_at"]).total_seconds() // 3600)}
        for row in db.list_open_questions(user_id)
    ]


async def search_answered_questions(repo: str, text: str) -> list[dict[str, Any]]:
    """Return the answered questions in *repo* matching *text*, newest answer first.

    Each carries its answer, answerer, ``answered_at`` and outcome.
    """
    text = text.strip()
    if not text:
        raise ValueError("text must not be empty")
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    return db.search_answered_questions(user_id, repo, text)


async def answer_author(repo: str, number: int, answer: str) -> dict[str, Any]:
    """Answer the open question blocking issue *number*; see ``answer_question``.

    Refused unless the issue is waiting on the author, or when several open
    questions block it, since one answer cannot say which it settles. An issue
    labelled ``needs-author`` with no open question behind it — by hand on GitHub,
    or left labelled by a failed answer — gets the answer and loses the label
    directly.

    Returns ``{"url"}`` of the answer comment on *number*.
    """
    if not answer.strip():
        raise ValueError("answer must not be empty")
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    issue = await github.get_issue(repo, number)
    if issue is None:
        raise FileNotFoundError(f"Issue #{number} not found in {repo!r}")
    if _NEEDS_AUTHOR_LABEL not in issue["labels"]:
        raise ValueError(f"Issue #{number} in {repo!r} is not waiting on the author")
    open_ids = db.open_questions_blocking(user_id, repo, number)
    if len(open_ids) > 1:
        raise ValueError(
            f"Issue #{number} in {repo!r} is blocked by questions "
            f"{', '.join(map(str, open_ids))}; answer each with answer_question"
        )
    if open_ids:
        result = await answer_question(repo, open_ids[0], answer, "author")
        return {"url": next(c["url"] for c in result["comments"] if c["issue"] == number)}
    return await _answer_unrecorded(repo, number, answer)


async def _answer_unrecorded(repo: str, number: int, answer: str) -> dict[str, Any]:
    """Post *answer* on issue *number* and remove its ``needs-author`` label.

    Either both land or neither does: if the answer cannot be posted, the label
    is put back before the failure is raised. If putting it back fails too, a
    ``RuntimeError`` names both failures.
    """
    await github.remove_label(repo, number, _NEEDS_AUTHOR_LABEL)
    try:
        url = await github.create_issue_comment(repo, number, answer)
        if url is None:
            raise FileNotFoundError(f"Issue #{number} not found in {repo!r}")
    except Exception as failure:
        try:
            await github.add_labels(repo, number, [_NEEDS_AUTHOR_LABEL])
        except Exception as restore_failure:
            raise RuntimeError(
                f"Answer to #{number} in {repo!r} failed ({failure!r}) and putting the "
                f"needs-author label back also failed ({restore_failure!r})"
            ) from failure
        raise
    return {"url": url}
