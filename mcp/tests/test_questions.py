"""Tests for mcp/tools/questions.py — asking the author questions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

import notify
from auth import current_db_user_id
from tools.questions import (
    _QUESTION_MAX_CHARS,
    answer_author,
    answer_question,
    ask_author,
    ask_question,
    list_defaulted_questions,
    list_open_questions,
    search_answered_questions,
    settle_expired_defaults,
)

_REAL_SEND = notify.send
_REGISTERED = [{"id": "r1", "repo_name": "alexsiri7/lachesis", "created_at": "t"}]
_ISSUES_URL = "https://api.github.com/repos/alexsiri7/lachesis/issues"
_LABELS_URL = "https://api.github.com/repos/alexsiri7/lachesis/labels"


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


@pytest.fixture()
def registered():
    with patch("tools.requirements.db.list_repos", return_value=_REGISTERED):
        yield


@pytest.fixture()
def record_question():
    with patch("tools.questions.db.record_question", return_value={"id": 7}) as mock:
        yield mock


@pytest.fixture(autouse=True)
def sent():
    with patch("tools.questions.notify.send", new_callable=AsyncMock, return_value=True) as mock:
        yield mock


def _comment_url(number: int) -> str:
    return f"https://github.com/alexsiri7/lachesis/issues/{number}#issuecomment-{number}"


def _mock_comment(httpx_mock: HTTPXMock, number: int, status_code: int = 201) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{_ISSUES_URL}/{number}/comments",
        status_code=status_code,
        json={"html_url": _comment_url(number)},
    )


def _mock_labelling(httpx_mock: HTTPXMock, *numbers: int) -> None:
    httpx_mock.add_response(method="POST", url=_LABELS_URL, status_code=201)
    for number in numbers:
        httpx_mock.add_response(method="POST", url=f"{_ISSUES_URL}/{number}/labels", json=[])


def _requests(httpx_mock: HTTPXMock) -> list[tuple[str, str]]:
    return [(r.method, str(r.url)) for r in httpx_mock.get_requests()]


# ---------------------------------------------------------------------------
# ask_question
# ---------------------------------------------------------------------------


async def test_asking_with_options(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    """Scenario: Asking with options — every field is stored, issue 160 as blocked."""
    _mock_comment(httpx_mock, 160)
    _mock_labelling(httpx_mock, 160)

    result = await ask_question(
        "lachesis",
        "Does this change contradict the backlog capability?",
        "spec_contradiction",
        "archon worker",
        blocks=[160],
        context="The backlog spec says new issues are ready by default.",
        options=["It contradicts it", "It does not"],
        capability="backlog",
    )

    assert result == {"id": 7, "comments": [{"issue": 160, "url": _comment_url(160)}]}
    record_question.assert_called_once_with(
        "test-uid",
        "alexsiri7/lachesis",
        "Does this change contradict the backlog capability?",
        "spec_contradiction",
        "archon worker",
        context="The backlog spec says new issues are ready by default.",
        options=["It contradicts it", "It does not"],
        capability="backlog",
        spec_change=None,
        milestone=None,
        issue=None,
        blocks=[160],
        default_answer=None,
        wait=None,
    )
    assert json.loads(httpx_mock.get_requests()[0].content) == {
        "body": "Does this change contradict the backlog capability?\n\n"
        "The backlog spec says new issues are ready by default.\n\n"
        "Options:\n- It contradicts it\n- It does not"
    }


async def test_one_question_two_issues(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    """Scenario: One question, two issues — both carry the question and needs-author."""
    _mock_comment(httpx_mock, 160)
    _mock_comment(httpx_mock, 161)
    _mock_labelling(httpx_mock, 160, 161)

    result = await ask_question(
        "alexsiri7/lachesis", "Split this work?", "large_work", "archon", blocks=[160, 161]
    )

    assert [comment["issue"] for comment in result["comments"]] == [160, 161]
    assert _requests(httpx_mock) == [
        ("POST", f"{_ISSUES_URL}/160/comments"),
        ("POST", f"{_ISSUES_URL}/161/comments"),
        ("POST", _LABELS_URL),
        ("POST", f"{_ISSUES_URL}/160/labels"),
        ("POST", f"{_ISSUES_URL}/161/labels"),
    ]
    for request in httpx_mock.get_requests()[3:]:
        assert json.loads(request.content) == {"labels": ["needs-author"]}


async def test_a_question_is_recorded_before_any_issue_is_labelled(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, sent: AsyncMock
) -> None:
    """An issue must never carry needs-author without a recorded question."""
    _mock_comment(httpx_mock, 160)
    calls_at_record: list[int] = []

    def record(*args, **kwargs):
        calls_at_record.append(len(httpx_mock.get_requests()))
        raise RuntimeError("database down")

    with (
        patch("tools.questions.db.record_question", side_effect=record),
        pytest.raises(RuntimeError, match="database down"),
    ):
        await ask_question("lachesis", "A or B?", "clarification", "archon", blocks=[160])

    assert calls_at_record == [1]
    assert _requests(httpx_mock) == [("POST", f"{_ISSUES_URL}/160/comments")]
    sent.assert_not_awaited()


async def test_a_missing_blocked_issue_records_and_labels_nothing(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question, sent: AsyncMock
) -> None:
    _mock_comment(httpx_mock, 160)
    _mock_comment(httpx_mock, 999, status_code=404)

    with pytest.raises(FileNotFoundError, match="#999"):
        await ask_question("lachesis", "A or B?", "clarification", "archon", blocks=[160, 999])

    record_question.assert_not_called()
    assert "labels" not in " ".join(url for _, url in _requests(httpx_mock))
    sent.assert_not_awaited()


async def test_a_question_blocking_nothing_is_only_recorded(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    result = await ask_question("lachesis", "Ship a CLI too?", "improvement", "archon")

    assert result == {"id": 7, "comments": []}
    assert record_question.call_args.kwargs["blocks"] == []
    assert httpx_mock.get_requests() == []


async def test_asking_for_more_budget(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question, sent: AsyncMock
) -> None:
    """Scenario: Asking for more budget — stored with what it delivers, issue 160 not labelled."""
    result = await ask_question(
        "lachesis",
        "Raise the spending cap to $40 this week?",
        "budget",
        "archon",
        context="The extra spend would deliver #160 two days sooner.",
        issue=160,
    )

    assert result == {"id": 7, "comments": []}
    assert record_question.call_args.args[3] == "budget"
    assert record_question.call_args.kwargs["context"] == (
        "The extra spend would deliver #160 two days sooner."
    )
    assert record_question.call_args.kwargs["blocks"] == []
    assert httpx_mock.get_requests() == []
    sent.assert_awaited_once()
    assert sent.call_args.kwargs["title"] == "Budget question on alexsiri7/lachesis"


async def test_a_budget_question_blocking_an_issue_is_refused(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    with pytest.raises(ValueError, match="blocks no issue"):
        await ask_question(
            "lachesis", "More budget?", "budget", "archon", blocks=[160], context="Ships #160."
        )

    record_question.assert_not_called()
    assert httpx_mock.get_requests() == []


async def test_a_budget_question_must_say_what_the_spend_delivers(
    set_user_id: str, registered: None, record_question
) -> None:
    with pytest.raises(ValueError, match="what the extra spend would deliver"):
        await ask_question("lachesis", "More budget?", "budget", "archon", context=" ")

    record_question.assert_not_called()


async def test_a_blocked_issue_named_twice_is_asked_once(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    _mock_comment(httpx_mock, 160)
    _mock_labelling(httpx_mock, 160)

    await ask_question("lachesis", "A or B?", "clarification", "archon", blocks=[160, 160])

    assert record_question.call_args.kwargs["blocks"] == [160]
    assert len(httpx_mock.get_requests()) == 3


async def test_an_unknown_kind_is_refused_before_anything_happens(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    with pytest.raises(ValueError, match="kind must be one of"):
        await ask_question("lachesis", "A or B?", "clarifcation", "archon", blocks=[160])  # type: ignore[arg-type]

    record_question.assert_not_called()
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize(
    ("question", "asker", "message"),
    [(" ", "archon", "question must not be empty"), ("A or B?", " ", "asker must not be empty")],
)
async def test_an_empty_question_or_asker_is_refused(
    set_user_id: str, record_question, question: str, asker: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        await ask_question("lachesis", question, "clarification", asker, blocks=[160])

    record_question.assert_not_called()


async def test_asking_is_refused_for_an_unregistered_repository(
    set_user_id: str, registered: None, record_question
) -> None:
    with pytest.raises(ValueError, match="'someone/else' is not registered"):
        await ask_question("someone/else", "A or B?", "clarification", "archon", blocks=[1])

    record_question.assert_not_called()


# ---------------------------------------------------------------------------
# ask_author
# ---------------------------------------------------------------------------


async def test_asking(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    """Scenario: Asking — the question is the latest comment, the label is on, and an
    open question record blocks the issue."""
    _mock_comment(httpx_mock, 160)
    _mock_labelling(httpx_mock, 160)

    result = await ask_author("alexsiri7/lachesis", 160, "Behaviour A or B?")

    assert result == {"url": _comment_url(160)}
    requests = httpx_mock.get_requests()
    assert json.loads(requests[0].content) == {"body": "Behaviour A or B?"}
    assert json.loads(requests[1].content) == {"name": "needs-author", "color": "fbca04"}
    assert json.loads(requests[2].content) == {"labels": ["needs-author"]}
    args = record_question.call_args
    assert args.args[3:] == ("clarification", "agent")
    assert args.kwargs["blocks"] == [160]
    assert args.kwargs["issue"] == 160


async def test_asking_does_not_label_when_the_question_fails(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    """An issue must never carry needs-author without its question."""
    _mock_comment(httpx_mock, 160, status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        await ask_author("alexsiri7/lachesis", 160, "Behaviour A or B?")

    assert _requests(httpx_mock) == [("POST", f"{_ISSUES_URL}/160/comments")]
    record_question.assert_not_called()


async def test_asking_reports_a_labelling_failure_after_the_question(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    """If labelling fails, the question stays and the failure is raised."""
    _mock_comment(httpx_mock, 160)
    httpx_mock.add_response(method="POST", url=_LABELS_URL, status_code=201)
    httpx_mock.add_response(method="POST", url=f"{_ISSUES_URL}/160/labels", status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        await ask_author("alexsiri7/lachesis", 160, "Behaviour A or B?")

    assert "DELETE" not in [method for method, _ in _requests(httpx_mock)]
    record_question.assert_called_once()


async def test_a_labelling_failure_part_way_leaves_earlier_labels(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    """Labelling the second issue fails: the first keeps its label and the failure is raised."""
    _mock_comment(httpx_mock, 160)
    _mock_comment(httpx_mock, 161)
    _mock_labelling(httpx_mock, 160)
    httpx_mock.add_response(method="POST", url=f"{_ISSUES_URL}/161/labels", status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        await ask_question(
            "alexsiri7/lachesis", "Split this work?", "large_work", "archon", blocks=[160, 161]
        )

    assert _requests(httpx_mock) == [
        ("POST", f"{_ISSUES_URL}/160/comments"),
        ("POST", f"{_ISSUES_URL}/161/comments"),
        ("POST", _LABELS_URL),
        ("POST", f"{_ISSUES_URL}/160/labels"),
        ("POST", f"{_ISSUES_URL}/161/labels"),
    ]
    record_question.assert_called_once()


async def test_asking_raises_when_issue_missing(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    _mock_comment(httpx_mock, 160, status_code=404)

    with pytest.raises(FileNotFoundError, match="#160"):
        await ask_author("alexsiri7/lachesis", 160, "Behaviour A or B?")

    assert len(httpx_mock.get_requests()) == 1
    record_question.assert_not_called()


# ---------------------------------------------------------------------------
# the author is told at once
# ---------------------------------------------------------------------------


async def test_new_question(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question, sent: AsyncMock
) -> None:
    """Scenario: New question — a push names its repository, question and kind."""
    _mock_comment(httpx_mock, 160)
    _mock_comment(httpx_mock, 161)
    _mock_labelling(httpx_mock, 160, 161)

    await ask_question(
        "lachesis", " Which spec wins? ", "spec_contradiction", "archon", blocks=[160, 161]
    )

    sent.assert_awaited_once_with(
        "test-uid",
        title="Spec contradiction question on alexsiri7/lachesis blocking #160, #161",
        body="Which spec wins?",
        click="https://github.com/alexsiri7/lachesis/issues/160",
        tags=["question"],
    )


@pytest.mark.parametrize(
    ("issue", "click"), [(None, None), (42, "https://github.com/alexsiri7/lachesis/issues/42")]
)
async def test_a_question_blocking_nothing_still_reaches_the_author(
    set_user_id: str,
    registered: None,
    record_question,
    sent: AsyncMock,
    issue: int | None,
    click: str | None,
) -> None:
    await ask_question("lachesis", "Ship a CLI too?", "improvement", "archon", issue=issue)

    sent.assert_awaited_once()
    assert sent.await_args.kwargs["title"] == "Improvement question on alexsiri7/lachesis"
    assert sent.await_args.kwargs["click"] == click


async def test_worker_asks_overnight(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question, sent: AsyncMock
) -> None:
    """Scenario: Worker asks overnight — one push names the repo and issue, gives the
    question and links to the issue."""
    _mock_comment(httpx_mock, 160)
    _mock_labelling(httpx_mock, 160)

    await ask_author("alexsiri7/lachesis", 160, "Should a parked issue keep its position?")

    sent.assert_awaited_once()
    kwargs = sent.await_args.kwargs
    assert "alexsiri7/lachesis" in kwargs["title"]
    assert "#160" in kwargs["title"]
    assert kwargs["body"] == "Should a parked issue keep its position?"
    assert kwargs["click"] == "https://github.com/alexsiri7/lachesis/issues/160"


async def test_the_author_is_told_before_any_issue_is_labelled(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question, sent: AsyncMock
) -> None:
    _mock_comment(httpx_mock, 160)
    httpx_mock.add_response(method="POST", url=_LABELS_URL, status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        await ask_author("alexsiri7/lachesis", 160, "Behaviour A or B?")

    sent.assert_awaited_once()


async def test_a_long_question_is_truncated_behind_the_link(
    set_user_id: str, registered: None, record_question, sent: AsyncMock
) -> None:
    await ask_question("lachesis", " ".join(["why"] * 1000), "clarification", "archon")

    body = sent.await_args.kwargs["body"]
    assert body.endswith("...")
    assert len(body) <= _QUESTION_MAX_CHARS + 3


async def test_notification_service_down_still_asks_the_question(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    _mock_comment(httpx_mock, 160)
    httpx_mock.add_exception(httpx.ConnectError("unreachable"), url="https://ntfy.example/lachesis")
    _mock_labelling(httpx_mock, 160)
    config = {"server": "https://ntfy.example", "topic": "lachesis", "token": ""}
    with (
        patch("tools.questions.notify.send", _REAL_SEND),
        patch("notify.db.get_ntfy_config", return_value=config),
    ):
        result = await ask_author("alexsiri7/lachesis", 160, "Behaviour A or B?")

    assert result == {"url": _comment_url(160)}
    assert ("POST", "https://ntfy.example/lachesis") in _requests(httpx_mock)


# ---------------------------------------------------------------------------
# answer_question
# ---------------------------------------------------------------------------


def _open_question(blocks: list[int]) -> dict:
    return {"id": 7, "repo_name": "alexsiri7/lachesis", "blocks": blocks, "answered_at": None}


@pytest.fixture()
def answering():
    """Patch the question store: question 7 is open and blocks issues 160 and 161."""
    with (
        patch(
            "tools.questions.db.get_question", return_value=_open_question([160, 161])
        ) as get_question,
        patch(
            "tools.questions.db.answer_question",
            return_value={**_open_question([160, 161]), "released": [160, 161]},
        ) as answer,
    ):
        yield get_question, answer


def _mock_unlabel(httpx_mock: HTTPXMock, number: int, status_code: int = 200) -> None:
    httpx_mock.add_response(
        method="DELETE",
        url=f"{_ISSUES_URL}/{number}/labels/needs-author",
        status_code=status_code,
        json=[],
    )


async def test_answer_that_becomes_a_rule(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, answering
) -> None:
    """Scenario: Answer that becomes a rule — the answer, answerer and outcome are
    recorded, every blocked issue carries the answer and is released."""
    _, answer = answering
    _mock_comment(httpx_mock, 160)
    _mock_comment(httpx_mock, 161)
    _mock_unlabel(httpx_mock, 160)
    _mock_unlabel(httpx_mock, 161)

    result = await answer_question(
        "alexsiri7/lachesis", 7, " Always A. ", "alex", outcome="standing_rule"
    )

    assert result == {
        "id": 7,
        "outcome": "standing_rule",
        "comments": [
            {"issue": 160, "url": _comment_url(160)},
            {"issue": 161, "url": _comment_url(161)},
        ],
        "released": [160, 161],
    }
    answer.assert_called_once_with(
        "test-uid", "alexsiri7/lachesis", 7, "Always A.", "alex", "standing_rule"
    )
    requests = httpx_mock.get_requests()
    assert [json.loads(r.content) for r in requests[:2]] == [{"body": "Always A."}] * 2
    assert _requests(httpx_mock)[2:] == [
        ("DELETE", f"{_ISSUES_URL}/160/labels/needs-author"),
        ("DELETE", f"{_ISSUES_URL}/161/labels/needs-author"),
    ]


async def test_two_questions_one_issue(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, answering
) -> None:
    """Scenario: Two questions, one issue — an issue another open question still
    blocks gets the answer but keeps needs-author."""
    _, answer = answering
    answer.return_value = {**_open_question([160, 161]), "released": [161]}
    _mock_comment(httpx_mock, 160)
    _mock_comment(httpx_mock, 161)
    _mock_unlabel(httpx_mock, 161)

    result = await answer_question("alexsiri7/lachesis", 7, "Always A.", "alex")

    assert result["released"] == [161]
    assert ("DELETE", f"{_ISSUES_URL}/160/labels/needs-author") not in _requests(httpx_mock)


async def test_an_answer_is_posted_before_it_is_recorded_and_released(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, answering
) -> None:
    """An issue must never lose needs-author without its answer."""
    _, answer = answering
    _mock_comment(httpx_mock, 160)
    _mock_comment(httpx_mock, 161, status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        await answer_question("alexsiri7/lachesis", 7, "Always A.", "alex")

    answer.assert_not_called()
    assert "DELETE" not in [method for method, _ in _requests(httpx_mock)]


async def test_an_answered_question_is_refused_before_anything_happens(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, answering
) -> None:
    get_question, answer = answering
    get_question.return_value = {
        **_open_question([160]),
        "answered_at": "2026-10-03",
        "outcome": "none",
    }

    with pytest.raises(ValueError, match="already answered"):
        await answer_question("alexsiri7/lachesis", 7, "Always A.", "alex")

    assert httpx_mock.get_requests() == []
    answer.assert_not_called()


async def test_an_unknown_question_is_refused(
    set_user_id: str, registered: None, answering
) -> None:
    get_question, answer = answering
    get_question.return_value = None

    with pytest.raises(FileNotFoundError, match="Question 7"):
        await answer_question("alexsiri7/lachesis", 7, "Always A.", "alex")

    answer.assert_not_called()


@pytest.mark.parametrize(
    ("answer", "answerer", "outcome", "message"),
    [
        ("  ", "alex", "none", "answer must not be empty"),
        ("A.", " ", "none", "answerer must not be empty"),
        ("A.", "alex", "rule", "outcome must be one of"),
    ],
)
async def test_an_incomplete_answer_is_refused(
    set_user_id: str, answering, answer: str, answerer: str, outcome: str, message: str
) -> None:
    get_question, record = answering
    with pytest.raises(ValueError, match=message):
        await answer_question("alexsiri7/lachesis", 7, answer, answerer, outcome)  # type: ignore[arg-type]

    get_question.assert_not_called()
    record.assert_not_called()


# ---------------------------------------------------------------------------
# answer_author
# ---------------------------------------------------------------------------

_ISSUE_160_URL = f"{_ISSUES_URL}/160"
_NEEDS_AUTHOR_ON_160 = f"{_ISSUE_160_URL}/labels/needs-author"


def _mock_issue_160(httpx_mock: HTTPXMock, labels: list[str]) -> None:
    httpx_mock.add_response(
        method="GET",
        url=_ISSUE_160_URL,
        json={
            "number": 160,
            "id": 1160,
            "title": "Issue 160",
            "state": "open",
            "labels": [{"name": name} for name in labels],
            "html_url": "https://github.com/alexsiri7/lachesis/issues/160",
        },
    )


@pytest.fixture()
def blocking():
    with patch("tools.questions.db.open_questions_blocking", return_value=[]) as mock:
        yield mock


async def test_answering(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, blocking, answering
) -> None:
    """Scenario: Answering — the open question blocking the issue is answered by the
    author with outcome none, the answer is the latest comment and the label is gone."""
    _, answer = answering
    blocking.return_value = [7]
    _mock_issue_160(httpx_mock, ["needs-author"])
    _mock_comment(httpx_mock, 160)
    _mock_comment(httpx_mock, 161)
    _mock_unlabel(httpx_mock, 160)
    _mock_unlabel(httpx_mock, 161)

    result = await answer_author("alexsiri7/lachesis", 160, "Behaviour A.")

    assert result == {"url": _comment_url(160)}
    blocking.assert_called_once_with("test-uid", "alexsiri7/lachesis", 160)
    answer.assert_called_once_with(
        "test-uid", "alexsiri7/lachesis", 7, "Behaviour A.", "author", "none"
    )


async def test_answering_is_refused_when_several_questions_block_the_issue(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, blocking, answering
) -> None:
    _, answer = answering
    blocking.return_value = [7, 9]
    _mock_issue_160(httpx_mock, ["needs-author"])

    with pytest.raises(ValueError, match="questions 7, 9; answer each with answer_question"):
        await answer_author("alexsiri7/lachesis", 160, "Behaviour A.")

    assert [method for method, _ in _requests(httpx_mock)] == ["GET"]
    answer.assert_not_called()


async def test_answering_an_issue_with_no_recorded_question(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, blocking
) -> None:
    """A needs-author label added by hand still gets the answer and loses the label."""
    _mock_issue_160(httpx_mock, ["needs-author"])
    _mock_unlabel(httpx_mock, 160)
    _mock_comment(httpx_mock, 160)

    result = await answer_author("alexsiri7/lachesis", 160, "Behaviour A.")

    assert result == {"url": _comment_url(160)}
    assert json.loads(httpx_mock.get_requests()[-1].content) == {"body": "Behaviour A."}


async def test_answering_puts_the_label_back_when_the_answer_fails(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, blocking
) -> None:
    """An issue must never lose needs-author without its answer."""
    _mock_issue_160(httpx_mock, ["needs-author"])
    _mock_unlabel(httpx_mock, 160)
    _mock_comment(httpx_mock, 160, status_code=500)
    httpx_mock.add_response(method="POST", url=f"{_ISSUE_160_URL}/labels", json=[])

    with pytest.raises(httpx.HTTPStatusError):
        await answer_author("alexsiri7/lachesis", 160, "Behaviour A.")

    assert json.loads(httpx_mock.get_requests()[-1].content) == {"labels": ["needs-author"]}


async def test_answering_names_both_failures_when_the_label_cannot_be_put_back(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, blocking
) -> None:
    _mock_issue_160(httpx_mock, ["needs-author"])
    _mock_unlabel(httpx_mock, 160)
    _mock_comment(httpx_mock, 160, status_code=500)
    httpx_mock.add_response(method="POST", url=f"{_ISSUE_160_URL}/labels", status_code=502)

    with pytest.raises(RuntimeError, match="needs-author label back also failed") as raised:
        await answer_author("alexsiri7/lachesis", 160, "Behaviour A.")

    assert isinstance(raised.value.__cause__, httpx.HTTPStatusError)
    assert raised.value.__cause__.response.status_code == 500


async def test_answering_is_refused_when_the_issue_is_not_waiting(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, blocking
) -> None:
    _mock_issue_160(httpx_mock, [])

    with pytest.raises(ValueError, match="not waiting on the author"):
        await answer_author("alexsiri7/lachesis", 160, "Behaviour A.")

    assert [method for method, _ in _requests(httpx_mock)] == ["GET"]
    blocking.assert_not_called()


# ---------------------------------------------------------------------------
# One queue of open questions; questions are not asked twice
# ---------------------------------------------------------------------------


async def test_reading_the_queue(set_user_id: str) -> None:
    """Scenario: Reading the queue — every open question, oldest first, with its kind,
    what it is about, the issues it blocks and how long it has waited."""
    now = datetime.now(UTC)
    rows = [
        {
            "id": 3,
            "repo_name": "alexsiri7/lachesis",
            "kind": "spec_contradiction",
            "capability": "questions",
            "blocks": [160],
            "created_at": now - timedelta(hours=30, minutes=1),
        },
        {
            "id": 9,
            "repo_name": "alexsiri7/kindred",
            "kind": "maturity",
            "capability": None,
            "blocks": [],
            "created_at": now - timedelta(hours=2, minutes=1),
        },
    ]
    with patch("tools.questions.db.list_open_questions", return_value=rows) as queue:
        result = await list_open_questions()

    queue.assert_called_once_with("test-uid")
    assert [q["id"] for q in result] == [3, 9]
    assert [q["waiting_hours"] for q in result] == [30, 2]
    assert result[0]["kind"] == "spec_contradiction"
    assert result[0]["blocks"] == [160]


async def test_already_answered(set_user_id: str, registered: None) -> None:
    """Scenario: Already answered — searching returns the question with its answer and
    outcome."""
    answered = {
        "id": 4,
        "question": "May the backlog use priority labels?",
        "answer": "Yes, as a standing rule.",
        "outcome": "standing_rule",
    }
    with patch("tools.questions.db.search_answered_questions", return_value=[answered]) as search:
        result = await search_answered_questions("lachesis", " priority labels ")

    search.assert_called_once_with("test-uid", "alexsiri7/lachesis", "priority labels")
    assert result == [answered]


async def test_searching_for_nothing_is_refused(set_user_id: str, registered: None) -> None:
    with patch("tools.questions.db.search_answered_questions") as search:
        with pytest.raises(ValueError, match="text must not be empty"):
            await search_answered_questions("lachesis", "   ")
    search.assert_not_called()


async def test_searching_is_refused_for_an_unregistered_repository(
    set_user_id: str, registered: None
) -> None:
    with patch("tools.questions.db.search_answered_questions") as search:
        with pytest.raises(ValueError, match="'someone/else' is not registered"):
            await search_answered_questions("someone/else", "priority labels")
    search.assert_not_called()


# ---------------------------------------------------------------------------
# Defaults: small questions settle while the author is away
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["clarification", "improvement"])
async def test_a_small_question_carries_its_default_and_wait(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question, kind: str
) -> None:
    _mock_comment(httpx_mock, 160)
    _mock_labelling(httpx_mock, 160)

    await ask_question(
        "lachesis", "CSV or JSON?", kind, "agent", blocks=[160], default=" CSV ", wait_days=2
    )

    kwargs = record_question.call_args.kwargs
    assert (kwargs["default_answer"], kwargs["wait"]) == ("CSV", timedelta(days=2))
    comment = json.loads(httpx_mock.get_requests()[0].content)["body"]
    assert "Unless answered within 2 days, the default is taken: CSV" in comment


async def test_the_default_wait_is_three_days(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question
) -> None:
    _mock_comment(httpx_mock, 160)
    _mock_labelling(httpx_mock, 160)

    await ask_author("lachesis", 160, "CSV or JSON?", default="CSV")

    assert record_question.call_args.kwargs["wait"] == timedelta(days=3)


@pytest.mark.parametrize("kind", ["spec_contradiction", "large_work", "maturity", "budget"])
async def test_a_question_that_decides_the_project_refuses_a_default(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, record_question, kind: str
) -> None:
    """Scenario: Spec decision waits — the default is refused and nothing is asked."""
    with pytest.raises(ValueError, match="cannot carry a default"):
        await ask_question("lachesis", "Which?", kind, "agent", context="c", default="A")

    assert httpx_mock.get_requests() == []
    record_question.assert_not_called()


@pytest.mark.parametrize(("default", "wait_days"), [("  ", 3), ("A", 0), ("A", -1)])
async def test_an_empty_default_or_a_wait_that_is_not_positive_is_refused(
    set_user_id: str, registered: None, record_question, default: str, wait_days: float
) -> None:
    with pytest.raises(ValueError):
        await ask_question(
            "lachesis", "Which?", "clarification", "agent", default=default, wait_days=wait_days
        )

    record_question.assert_not_called()


def _expired(question_id: int, blocks: list[int]) -> dict:
    return {
        "id": question_id,
        "repo_name": "alexsiri7/lachesis",
        "blocks": blocks,
        "default_answer": "CSV",
    }


async def test_an_expired_default_is_taken_and_its_issue_released(
    httpx_mock: HTTPXMock,
) -> None:
    """Scenario: Away for a week — note posted, default recorded, issue released."""
    _mock_comment(httpx_mock, 160)
    _mock_unlabel(httpx_mock, 160)
    with (
        patch("tools.questions.db.list_expired_defaults", return_value=[_expired(7, [160])]),
        patch(
            "tools.questions.db.answer_question",
            return_value={**_expired(7, [160]), "released": [160]},
        ) as answer,
    ):
        settled = await settle_expired_defaults("test-uid")

    answer.assert_called_once_with(
        "test-uid", "alexsiri7/lachesis", 7, "CSV", "default", "defaulted"
    )
    assert [q["id"] for q in settled] == [7]
    comment, unlabel = httpx_mock.get_requests()
    assert "the default was taken: CSV" in json.loads(comment.content)["body"]
    assert unlabel.method == "DELETE"


async def test_a_default_that_fails_to_settle_is_left_for_the_next_read(
    httpx_mock: HTTPXMock,
) -> None:
    _mock_comment(httpx_mock, 160, status_code=500)
    _mock_comment(httpx_mock, 161)
    _mock_unlabel(httpx_mock, 161)
    expired = [_expired(7, [160]), _expired(8, [161])]
    with (
        patch("tools.questions.db.list_expired_defaults", return_value=expired),
        patch(
            "tools.questions.db.answer_question",
            return_value={**_expired(8, [161]), "released": [161]},
        ) as answer,
    ):
        settled = await settle_expired_defaults("test-uid")

    answer.assert_called_once()
    assert [q["id"] for q in settled] == [8]


async def test_settling_never_raises_when_the_questions_cannot_be_read() -> None:
    with patch("tools.questions.db.list_expired_defaults", side_effect=RuntimeError("down")):
        assert await settle_expired_defaults("test-uid") == []


async def test_reading_the_open_questions_settles_expired_defaults_first(
    set_user_id: str,
) -> None:
    with (
        patch("tools.questions.settle_expired_defaults", new_callable=AsyncMock) as settle,
        patch("tools.questions.db.list_open_questions", return_value=[]),
    ):
        await list_open_questions()

    settle.assert_awaited_once_with("test-uid")


async def test_the_author_may_answer_a_defaulted_question(
    httpx_mock: HTTPXMock, set_user_id: str, registered: None, answering
) -> None:
    get_question, answer = answering
    get_question.return_value = {
        **_open_question([160, 161]),
        "answered_at": "2026-10-03",
        "outcome": "defaulted",
    }
    for number in (160, 161):
        _mock_comment(httpx_mock, number)
        _mock_unlabel(httpx_mock, number)

    result = await answer_question("alexsiri7/lachesis", 7, "JSON after all.", "author")

    answer.assert_called_once()
    assert result["outcome"] == "none"


async def test_listing_defaults_settles_first_and_reads_the_window(set_user_id: str) -> None:
    with (
        patch("tools.questions.settle_expired_defaults", new_callable=AsyncMock) as settle,
        patch("tools.questions.db.list_defaulted_questions", return_value=[]) as listed,
    ):
        await list_defaulted_questions(since_hours=24)

    settle.assert_awaited_once_with("test-uid")
    since = listed.call_args.args[1]
    assert timedelta(hours=23) < datetime.now(UTC) - since < timedelta(hours=25)


async def test_listing_defaults_needs_a_window(set_user_id: str) -> None:
    with pytest.raises(ValueError, match="since_hours"):
        await list_defaulted_questions(since_hours=0)
