"""Author tasks: what only the author can do or provide (spec change #348).

The contract these tests set for the implementation issues:

- ``ask_question`` accepts kind ``author_task``. Like every kind but clarification and
  improvement, it refuses a ``default``, and the refusal names the default.
- ``list_open_questions(kind=None)`` takes an optional ``kind``; with one, only open
  questions of that kind are returned.
- The agent guidance (``get_initial_instructions``) names ``author_task`` where it tells
  agents to split the author-only steps out of an issue when triaging or sizing it, and
  where it tells a run that cannot continue without the author to ask one before it ends.

How the agent splits an issue or ends a run is the agent's to do; what Lachesis holds is
the guidance it reads and the author tasks it records.

The harness has no clock, so "asked a week ago" is a question asked with a wait of one
second, read two seconds later.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from harness import Lachesis, scenario

REPO = "owner/repo"
ISSUE = 160
ONE_SECOND_IN_DAYS = 1 / 86400


def _issue(number: int) -> dict[str, Any]:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": "open",
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "closed_at": None,
        "state_reason": None,
    }


async def _repo(lachesis: Lachesis, *numbers: int) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text
    github = lachesis.github
    github.on("GET", f"/repos/{REPO}/issues", json=[_issue(number) for number in numbers])
    github.on("GET", f"/repos/{REPO}/labels/needs-author", json={"name": "needs-author"})
    github.on("POST", f"/repos/{REPO}/labels", json={"name": "needs-author"}, status=201)
    for number in numbers:
        github.on("GET", f"/repos/{REPO}/issues/{number}", json=_issue(number))
        github.on(
            "POST",
            f"/repos/{REPO}/issues/{number}/comments",
            json={"id": number, "html_url": f"https://github.com/{REPO}/issues/{number}#c"},
            status=201,
        )
        github.on(
            "POST", f"/repos/{REPO}/issues/{number}/labels", json=[{"name": "needs-author"}]
        )


async def _ask_author_task(
    lachesis: Lachesis, question: str, context: str, blocks: list[int]
) -> None:
    asked = await lachesis.call_tool(
        "ask_question",
        {
            "repo": REPO,
            "question": question,
            "context": context,
            "kind": "author_task",
            "asker": "agent",
            "blocks": blocks,
        },
    )
    assert not asked.is_error, asked.text


async def _open_author_tasks(lachesis: Lachesis) -> list[dict[str, Any]]:
    listed = await lachesis.call_tool("list_open_questions", {"kind": "author_task"})
    assert not listed.is_error, listed.text
    return listed.json()


def _labelled(lachesis: Lachesis, number: int) -> bool:
    return any(
        "needs-author" in (request.body or {}).get("labels", [])
        for request in lachesis.github.requests_to(
            "POST", f"/repos/{REPO}/issues/{number}/labels"
        )
    )


async def _guidance_sentences(lachesis: Lachesis) -> list[str]:
    lachesis.github.on("GET", f"/repos/{REPO}/contents/openspec/specs", json=[])
    result = await lachesis.call_tool("get_initial_instructions", {"repo": REPO})
    assert not result.is_error, result.text
    text = " ".join(result.json().lower().split())
    return re.split(r"(?<=[.:;])\s", text)


@pytest.mark.xfail(strict=True, reason="spec change #348")
@scenario("questions", "Asking for a key")
async def test_asking_for_a_key(lachesis: Lachesis) -> None:
    await _repo(lachesis, ISSUE)

    await _ask_author_task(
        lachesis,
        "Set REQUESTY_API_KEY on the kith service in Railway",
        "Create a key at requesty.ai and set it as REQUESTY_API_KEY on the kith service; "
        "the agent knows it is done when the health check reports the key present.",
        [ISSUE],
    )

    [task] = await _open_author_tasks(lachesis)
    assert task["kind"] == "author_task"
    details = f"{task['question']} {task['context']}"
    for detail in ("REQUESTY_API_KEY", "kith", "Railway", "requesty.ai"):
        assert detail in details
    assert task["blocks"] == [ISSUE]
    assert _labelled(lachesis, ISSUE)


@pytest.mark.xfail(strict=True, reason="spec change #348")
@scenario("questions", "No default for an author task")
async def test_no_default_for_an_author_task(lachesis: Lachesis) -> None:
    await _repo(lachesis, ISSUE)
    task = {
        "repo": REPO,
        "question": "Add a CNAME for kith.example.com in Cloudflare",
        "kind": "author_task",
        "asker": "agent",
        "blocks": [ISSUE],
    }

    offered = await lachesis.call_tool(
        "ask_question", {**task, "default": "Skip the DNS record", "wait_days": ONE_SECOND_IN_DAYS}
    )
    asked = await lachesis.call_tool("ask_question", task)
    await asyncio.sleep(2)
    waiting = await lachesis.call_tool("list_open_questions", {})

    assert offered.is_error
    assert "default" in offered.text.lower()
    assert not asked.is_error, asked.text
    assert not waiting.is_error, waiting.text
    [open_task] = waiting.json()
    assert open_task["kind"] == "author_task"
    assert not lachesis.github.requests_to(
        "DELETE", f"/repos/{REPO}/issues/{ISSUE}/labels/needs-author"
    )


@pytest.mark.xfail(strict=True, reason="spec change #348")
@scenario("questions", "Provisioning split")
async def test_provisioning_split(lachesis: Lachesis) -> None:
    config, docs, health, verify = 160, 161, 162, 163
    await _repo(lachesis, config, docs, health, verify)

    sentences = await _guidance_sentences(lachesis)
    await _ask_author_task(
        lachesis,
        "Set REQUESTY_API_KEY on the kith service in Railway",
        "Create a key at requesty.ai.",
        [verify],
    )
    await _ask_author_task(
        lachesis,
        "Add a CNAME for kith.example.com in Cloudflare",
        "Point it at the Railway domain of the kith service.",
        [verify],
    )

    assert any("author_task" in s and ("triag" in s or "siz" in s) for s in sentences)
    tasks = await _open_author_tasks(lachesis)
    assert len(tasks) == 2
    assert all(task["blocks"] == [verify] for task in tasks)
    assert _labelled(lachesis, verify)
    assert not any(_labelled(lachesis, number) for number in (config, docs, health))


@pytest.mark.xfail(strict=True, reason="spec change #348")
@scenario("questions", "Missing access found mid-run")
async def test_missing_access_found_mid_run(lachesis: Lachesis) -> None:
    await _repo(lachesis, ISSUE)

    sentences = await _guidance_sentences(lachesis)
    await _ask_author_task(
        lachesis,
        "Add a CNAME for kith.example.com in Cloudflare",
        "The run on this issue has no access to the DNS provider.",
        [ISSUE],
    )

    assert any("author_task" in s and "run" in s for s in sentences)
    [task] = await _open_author_tasks(lachesis)
    assert "CNAME" in task["question"]
    assert task["blocks"] == [ISSUE]
    assert _labelled(lachesis, ISSUE)


@pytest.mark.xfail(strict=True, reason="spec change #348")
@scenario("questions", "Only my tasks")
async def test_only_my_tasks(lachesis: Lachesis) -> None:
    await _repo(lachesis, ISSUE)
    clarification = await lachesis.call_tool(
        "ask_question",
        {
            "repo": REPO,
            "question": "Should the export be CSV or JSON?",
            "kind": "clarification",
            "asker": "agent",
            "blocks": [ISSUE],
        },
    )
    assert not clarification.is_error, clarification.text
    await _ask_author_task(
        lachesis,
        "Set REQUESTY_API_KEY on the kith service in Railway",
        "Create a key at requesty.ai.",
        [ISSUE],
    )

    tasks = await _open_author_tasks(lachesis)

    assert [task["kind"] for task in tasks] == ["author_task"]
