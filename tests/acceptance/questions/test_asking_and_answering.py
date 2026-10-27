"""Asking the author, the one queue of open questions, and answering.

Each repository here is a small model of its open issues, their labels and comments, so a
label one call adds is the one next_issue reads, and an answer one call posts is the
latest comment on the issue.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from harness import Lachesis, scenario
from harness.github import NOT_FOUND, RecordedRequest

REPO = "owner/repo"
OTHER_REPO = "other/repo"
AUTHOR = "the-author"
NEEDS_AUTHOR = "needs-author"


class Repository:
    """GitHub as one repository's open issues, each with its labels and comments."""

    def __init__(self, lachesis: Lachesis, name: str, *numbers: int) -> None:
        self.name = name
        self.labels: dict[int, list[str]] = {number: [] for number in numbers}
        self.comments: dict[int, list[dict[str, Any]]] = {number: [] for number in numbers}
        lachesis.github.on("GET", "/user", json={"login": AUTHOR})
        lachesis.github.serve(f"/repos/{name}", self._answer)

    def _issue(self, number: int) -> dict[str, Any]:
        return {
            "number": number,
            "id": 1000 + number,
            "node_id": f"I_{number}",
            "title": f"Issue {number}",
            "body": "",
            "state": "open",
            "labels": [{"name": label} for label in self.labels[number]],
            "assignees": [],
            "html_url": f"https://github.com/{self.name}/issues/{number}",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "closed_at": None,
            "state_reason": None,
        }

    def _answer(self, request: RecordedRequest) -> tuple[int, Any]:
        rest = request.path.removeprefix(f"/repos/{self.name}").split("/")[1:]
        match request.method, rest:
            case "GET", ["issues"]:
                return 200, [self._issue(number) for number in self.labels]
            case "POST", ["labels"]:
                return 201, {"name": request.body["name"]}
            case _, ["issues", number, *more] if int(number) in self.labels:
                return self._on_issue(request.method, int(number), more, request.body)
        return 404, NOT_FOUND

    def _on_issue(self, method: str, number: int, rest: list[str], body: Any) -> tuple[int, Any]:
        match method, rest:
            case "GET", []:
                return 200, self._issue(number)
            case "GET", ["comments"]:
                return 200, self.comments[number]
            case "POST", ["comments"]:
                comment = self._comment(number, body["body"])
                self.comments[number].append(comment)
                return 201, comment
            case "POST", ["labels"]:
                for label in body["labels"]:
                    if label not in self.labels[number]:
                        self.labels[number].append(label)
                return 200, [{"name": label} for label in self.labels[number]]
            case "DELETE", ["labels", label] if label in self.labels[number]:
                self.labels[number].remove(label)
                return 200, [{"name": label} for label in self.labels[number]]
        return 404, NOT_FOUND

    def _comment(self, number: int, body: str) -> dict[str, Any]:
        comment_id = sum(len(comments) for comments in self.comments.values()) + 1
        created_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        return {
            "id": comment_id,
            "body": body,
            "user": {"login": AUTHOR},
            "created_at": created_at,
            "updated_at": created_at,
            "html_url": f"https://github.com/{self.name}/issues/{number}#issuecomment-{comment_id}",
        }


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


async def _registered(lachesis: Lachesis, name: str, *numbers: int) -> Repository:
    repo = Repository(lachesis, name, *numbers)
    await _ok(lachesis, "register_repo", {"repo": name})
    return repo


async def _ask(lachesis: Lachesis, question: str, **arguments: Any) -> int:
    asked = await _ok(
        lachesis,
        "ask_question",
        {
            "repo": REPO,
            "question": question,
            "kind": "clarification",
            "asker": "agent",
            **arguments,
        },
    )
    return int(asked["id"])


async def _open_questions(lachesis: Lachesis) -> list[dict[str, Any]]:
    return await _ok(lachesis, "list_open_questions", {})


async def _offered(lachesis: Lachesis) -> int | None:
    offered = await _ok(lachesis, "next_issue", {"repo": REPO})
    return offered["issue"]["number"] if offered["issue"] else None


@scenario("questions", "Asking with options")
async def test_asking_with_options(lachesis: Lachesis) -> None:
    await _registered(lachesis, REPO, 160)
    question_id = await _ask(
        lachesis,
        "Does this change contradict the backlog capability?",
        kind="spec_contradiction",
        asker="worker",
        context="The issue orders by priority label; the backlog spec orders by age.",
        options=["It contradicts the backlog spec", "It refines the backlog spec"],
        capability="backlog",
        blocks=[160],
    )

    [question] = await _open_questions(lachesis)

    assert question["id"] == question_id
    assert question["question"] == "Does this change contradict the backlog capability?"
    assert question["context"] == (
        "The issue orders by priority label; the backlog spec orders by age."
    )
    assert question["options"] == ["It contradicts the backlog spec", "It refines the backlog spec"]
    assert question["kind"] == "spec_contradiction"
    assert question["asker"] == "worker"
    assert question["blocks"] == [160]


@scenario("questions", "Asking for more budget")
async def test_asking_for_more_budget(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis, REPO, 160)
    await _ok(lachesis, "report_overflow", {"spent_usd": 50, "cap_usd": 50})
    delivers = "Another $20 of overflow would deliver issue 160 this sprint."
    await _ask(
        lachesis,
        "May the overflow cap rise from $50 to $70?",
        kind="budget",
        asker="factory",
        context=delivers,
    )

    [question] = await _open_questions(lachesis)

    assert question["kind"] == "budget"
    assert question["context"] == delivers
    assert question["blocks"] == []
    assert repo.labels[160] == []
    assert await _offered(lachesis) == 160


@scenario("questions", "One question, two issues")
async def test_one_question_two_issues(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis, REPO, 160, 161)
    assert await _offered(lachesis) == 160

    await _ask(lachesis, "Should the export be CSV or JSON?", blocks=[160, 161])

    assert repo.labels == {160: [NEEDS_AUTHOR], 161: [NEEDS_AUTHOR]}
    assert await _offered(lachesis) is None


@scenario("questions", "Two questions, one issue")
async def test_two_questions_one_issue(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis, REPO, 160)
    first = await _ask(lachesis, "Should the export be CSV or JSON?", blocks=[160])
    await _ask(lachesis, "Should the export include archived rows?", blocks=[160])

    answered = await _ok(
        lachesis,
        "answer_question",
        {"repo": REPO, "id": first, "answer": "CSV", "answerer": "author"},
    )

    assert answered["released"] == []
    assert repo.labels[160] == [NEEDS_AUTHOR]
    assert await _offered(lachesis) is None


@scenario("questions", "New question")
async def test_new_question(lachesis: Lachesis) -> None:
    await _registered(lachesis, REPO, 160)
    await _ok(lachesis, "set_ntfy_config", {"topic": "lachesis-author"})

    await _ask(lachesis, "Should the export be CSV or JSON?", blocks=[160])

    [notification] = lachesis.ntfy.notifications
    told = f"{notification.title} {notification.body}"
    assert REPO in told
    assert "Should the export be CSV or JSON?" in told
    assert "clarification" in told.lower()


@scenario("questions", "Reading the queue")
async def test_reading_the_queue(lachesis: Lachesis) -> None:
    await _registered(lachesis, REPO, 160)
    await _registered(lachesis, OTHER_REPO, 7)
    older = await _ask(
        lachesis, "Should the export be CSV or JSON?", capability="exports", blocks=[160]
    )
    newer = await _ask(lachesis, "Is #7 a duplicate of #3?", repo=OTHER_REPO, issue=7, blocks=[7])

    queue = await _open_questions(lachesis)

    assert [(question["repo_name"], question["id"]) for question in queue] == [
        (REPO, older),
        (OTHER_REPO, newer),
    ]
    first, second = queue
    assert (first["kind"], first["capability"], first["blocks"]) == (
        "clarification",
        "exports",
        [160],
    )
    assert (second["kind"], second["issue"], second["blocks"]) == ("clarification", 7, [7])
    assert first["waiting_hours"] == second["waiting_hours"] == 0


@scenario("questions", "Answer that becomes a rule")
async def test_answer_that_becomes_a_rule(lachesis: Lachesis) -> None:
    repo = await _registered(lachesis, REPO, 160)
    question_id = await _ask(lachesis, "May issues in this repo skip the changelog?", blocks=[160])
    rule = "Never skip the changelog."
    before = datetime.now(UTC)

    await _ok(
        lachesis,
        "answer_question",
        {
            "repo": REPO,
            "id": question_id,
            "answer": rule,
            "answerer": "author",
            "outcome": "standing_rule",
        },
    )

    [question] = await _ok(
        lachesis, "search_answered_questions", {"repo": REPO, "text": "changelog"}
    )
    assert question["id"] == question_id
    assert question["answer"] == rule
    assert question["answerer"] == "author"
    assert question["outcome"] == "standing_rule"
    assert datetime.fromisoformat(question["answered_at"]) >= before
    assert repo.comments[160][-1]["body"] == rule
    assert repo.labels[160] == []
    assert await _offered(lachesis) == 160


@scenario("questions", "Already answered")
async def test_already_answered(lachesis: Lachesis) -> None:
    await _registered(lachesis, REPO, 160, 161)
    priority = await _ask(lachesis, "May the backlog use priority labels?", blocks=[160])
    export = await _ask(lachesis, "Should the export be CSV or JSON?", blocks=[161])
    for question_id, answer in ((priority, "No, age decides."), (export, "CSV")):
        await _ok(
            lachesis,
            "answer_question",
            {
                "repo": REPO,
                "id": question_id,
                "answer": answer,
                "answerer": "author",
                "outcome": "standing_rule",
            },
        )

    found = await _ok(
        lachesis, "search_answered_questions", {"repo": REPO, "text": "priority labels"}
    )

    assert [(q["id"], q["answer"], q["outcome"]) for q in found] == [
        (priority, "No, age decides.", "standing_rule")
    ]


@scenario("questions", "Guidance says search first")
async def test_guidance_says_search_first(lachesis: Lachesis) -> None:
    guidance = await _ok(lachesis, "get_initial_instructions", {})

    sentences = " ".join(guidance.lower().split()).split(". ")
    assert any(
        "search_answered_questions" in sentence and "before asking" in sentence
        for sentence in sentences
    )
