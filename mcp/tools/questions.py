"""Lachesis MCP tool implementations — asking the author questions."""

from __future__ import annotations

from typing import Any, get_args

import db
import github
import landing
import notify

from .repos import _require_user_id
from .requirements import _NEEDS_AUTHOR_COLOR, _NEEDS_AUTHOR_LABEL, _resolve_repo

# ntfy turns a message over 4096 bytes into an attachment; the full question
# stays in its record and on any issue it names.
_QUESTION_MAX_CHARS = 1000


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
        body=landing._clip(question, _QUESTION_MAX_CHARS),
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
) -> dict[str, Any]:
    """Record a question for the author and mark every issue it *blocks* as waiting.

    Each blocked issue gets the question as a comment, then the question is
    recorded, then the author is sent a push (best effort, never raises), then
    each blocked issue is labelled ``needs-author``. That order keeps an issue
    from ever being labelled without both its question comment and its record;
    a failure part-way is raised and leaves what already landed.

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
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)

    body = _comment_body(question, context, options)
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
    )

    await _tell_author(user_id, repo, question, kind, blocked, issue)

    if blocked:
        await github.ensure_label(repo, _NEEDS_AUTHOR_LABEL, _NEEDS_AUTHOR_COLOR)
        for number in blocked:
            await github.add_labels(repo, number, [_NEEDS_AUTHOR_LABEL])
    return {"id": row["id"], "comments": comments}


async def ask_author(repo: str, number: int, question: str, asker: str = "agent") -> dict[str, Any]:
    """Ask a clarification question blocking issue *number*; see ``ask_question``.

    Returns ``{"url"}`` of the question comment.
    """
    result = await ask_question(
        repo, question, "clarification", asker, blocks=[number], issue=number
    )
    return {"url": result["comments"][0]["url"]}
