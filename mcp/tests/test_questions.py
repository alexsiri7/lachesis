"""Tests for mcp/tools/questions.py — asking the author questions."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

import notify
from auth import current_db_user_id
from tools.questions import _QUESTION_MAX_CHARS, ask_author, ask_question

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
    httpx_mock: HTTPXMock, set_user_id: str, registered: None
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
