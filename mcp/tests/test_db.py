"""Tests for mcp/db.py — stubs out the SQLAlchemy engine."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

import db


@pytest.fixture(autouse=True)
def clear_engine_cache():
    """Ensure lru_cache doesn't leak between tests."""
    db._engine.cache_clear()
    yield
    db._engine.cache_clear()


def _mock_engine(rows: list[dict]) -> MagicMock:
    """Return a MagicMock SQLAlchemy engine whose execute() returns the given rows.

    The engine supports both engine.begin() and engine.connect() as context
    managers, both returning the same mock connection.
    """
    # result.mappings().first() / .all()
    mock_mapping_result = MagicMock()
    if rows:
        first_row = rows[0]
        # mappings().first() returns a dict-like; we use a plain dict here
        # because db.py does dict(row) on the result of .first()
        mock_mapping_result.first.return_value = first_row
        mock_mapping_result.all.return_value = rows
    else:
        mock_mapping_result.first.return_value = None
        mock_mapping_result.all.return_value = []

    mock_result = MagicMock()
    mock_result.mappings.return_value = mock_mapping_result

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value = mock_result

    mock_engine = MagicMock()
    mock_engine.begin.return_value = mock_conn
    mock_engine.connect.return_value = mock_conn

    return mock_engine


def test_upsert_user_returns_row(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"id": "uuid-123", "google_id": "gid", "email": "a@b.com"}
    mock = _mock_engine([expected])
    with patch("db._engine", return_value=mock):
        result = db.upsert_user("gid", "a@b.com")
    assert result["id"] == "uuid-123"


def test_upsert_user_raises_on_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock):
        with pytest.raises(RuntimeError, match="upsert_user returned no row"):
            db.upsert_user("gid", "a@b.com")


def test_register_repo_returns_row() -> None:
    expected = {"id": "repo-uuid", "user_id": "uid", "repo_name": "owner/repo"}
    mock = _mock_engine([expected])
    with patch("db._engine", return_value=mock):
        result = db.register_repo("uid", "owner/repo")
    assert result["repo_name"] == "owner/repo"


def test_register_repo_leaves_an_omitted_landing_signal_alone() -> None:
    """An omitted signal binds NULL so COALESCE preserves the configured one."""
    mock = _mock_engine([{"repo_name": "owner/repo", "landing_signal": "deployment"}])
    with patch("db._engine", return_value=mock):
        db.register_repo("uid", "owner/repo")

    params = mock.begin.return_value.execute.call_args[0][1]
    assert params["landing_signal"] is None
    sql = " ".join(str(mock.begin.return_value.execute.call_args[0][0]).split())
    assert "COALESCE(:landing_signal, 'merge')" in sql
    assert "COALESCE( :landing_signal, lachesis_repos.landing_signal )" in sql


def test_register_repo_stores_a_given_landing_signal() -> None:
    mock = _mock_engine([{"repo_name": "owner/repo", "landing_signal": "deployment"}])
    with patch("db._engine", return_value=mock):
        result = db.register_repo("uid", "owner/repo", "deployment")

    assert result["landing_signal"] == "deployment"
    assert mock.begin.return_value.execute.call_args[0][1]["landing_signal"] == "deployment"


def test_get_landing_signal_defaults_to_merge_when_unregistered() -> None:
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock):
        assert db.get_landing_signal("uid", "owner/repo") == "merge"


def test_get_landing_signal_returns_the_stored_value() -> None:
    mock = _mock_engine([{"landing_signal": "deployment"}])
    with patch("db._engine", return_value=mock):
        assert db.get_landing_signal("uid", "owner/repo") == "deployment"


def test_notified_requirement_ids_returns_a_set() -> None:
    mock = _mock_engine([{"requirement_id": "026"}, {"requirement_id": "027"}])
    with patch("db._engine", return_value=mock):
        assert db.notified_requirement_ids("uid", "owner/repo") == {"026", "027"}


def test_get_ntfy_config_carries_the_epoch() -> None:
    """The row's creation time is what decides which landings are worth announcing."""
    created = datetime(2026, 9, 1, tzinfo=UTC)
    mock = _mock_engine([{"server": "s", "topic": "t", "token": "", "created_at": created}])
    with patch("db._engine", return_value=mock):
        config = db.get_ntfy_config("uid")

    assert config is not None
    assert config["created_at"] == created
    assert "created_at" in str(mock.connect.return_value.execute.call_args[0][0])


def test_list_repos_returns_list() -> None:
    rows = [{"id": "r1", "repo_name": "a/b", "created_at": "2026-05-14"}]
    mock = _mock_engine(rows)
    with patch("db._engine", return_value=mock):
        result = db.list_repos("uid")
    assert result == rows


def test_unregister_repo_no_exception() -> None:
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock):
        db.unregister_repo("uid", "owner/repo")  # should not raise


def test_engine_raises_if_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("db.settings.database_url", "")
    with pytest.raises(RuntimeError, match="not configured"):
        db._engine()


@pytest.mark.parametrize("scheme", ["postgresql", "postgres"])
def test_engine_uses_installed_psycopg2_driver_for_driverless_url(
    monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    """Builds a real engine (no connection) so a missing DBAPI import fails here, not on deploy."""
    monkeypatch.setattr("db.settings.database_url", f"{scheme}://u:p@localhost:5432/db")
    engine = db._engine()
    assert engine.dialect.driver == "psycopg2"
    assert engine.url.database == "db"


def test_sqlalchemy_database_url_keeps_explicit_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "postgresql+psycopg://u:p@localhost:5432/db"
    monkeypatch.setattr("db.settings.database_url", url)
    assert db.settings.sqlalchemy_database_url == url


def test_list_backlog_returns_rows() -> None:
    rows = [{"issue_number": 5, "position": 0, "ready": True, "interrupt": False}]
    mock = _mock_engine(rows)
    with patch("db._engine", return_value=mock):
        result = db.list_backlog("uid", "owner/repo")
    assert result == rows


def test_upsert_backlog_flags_leaves_omitted_flag_null() -> None:
    """An omitted flag binds NULL so COALESCE preserves the stored value."""
    row = {"issue_number": 5, "position": None, "ready": True, "interrupt": False}
    mock = _mock_engine([row])
    with patch("db._engine", return_value=mock):
        result = db.upsert_backlog_flags("uid", "owner/repo", 5, ready=True)

    assert result["interrupt"] is False
    params = mock.begin.return_value.execute.call_args[0][1]
    assert params["ready"] is True
    assert params["interrupt"] is None
    sql = str(mock.begin.return_value.execute.call_args[0][0])
    assert "COALESCE(:ready, lachesis_backlog.ready)" in sql
    assert "COALESCE(:interrupt, lachesis_backlog.interrupt)" in sql


def test_upsert_backlog_flags_inserts_ready_true_not_null() -> None:
    """A row created to set one flag must not bind NULL, nor park the issue."""
    row = {"issue_number": 9, "position": None, "ready": True, "interrupt": False}
    mock = _mock_engine([row])
    with patch("db._engine", return_value=mock):
        db.upsert_backlog_flags("uid", "owner/repo", 9, ready=True)

    sql = str(mock.begin.return_value.execute.call_args[0][0])
    assert "COALESCE(:ready, true), COALESCE(:interrupt, false)" in sql


def test_set_backlog_order_numbers_contiguously_and_clears_the_rest() -> None:
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock):
        db.set_backlog_order("uid", "owner/repo", [8, 3, 5])

    calls = mock.begin.return_value.execute.call_args_list
    assert "position = NULL" in str(calls[0][0][0])
    assert calls[0][0][1]["numbers"] == [8, 3, 5]
    placed = [(c[0][1]["issue_number"], c[0][1]["position"]) for c in calls[1:]]
    assert placed == [(8, 0), (3, 1), (5, 2)]


def test_get_ntfy_config_returns_none_when_never_configured() -> None:
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock):
        assert db.get_ntfy_config("uid") is None


def test_get_ntfy_config_returns_row() -> None:
    row = {"server": "https://ntfy.sh", "topic": "lachesis", "token": ""}
    mock = _mock_engine([row])
    with patch("db._engine", return_value=mock):
        result = db.get_ntfy_config("uid")
    assert result == row


def test_set_ntfy_config_upserts_on_user_id() -> None:
    row = {"server": "https://n.example", "topic": "lachesis", "token": "tk"}
    mock = _mock_engine([row])
    with patch("db._engine", return_value=mock):
        result = db.set_ntfy_config("uid", "https://n.example", "lachesis", "tk")

    assert result == row
    sql = str(mock.begin.return_value.execute.call_args[0][0])
    assert "ON CONFLICT (user_id) DO UPDATE" in sql


def test_claim_notification_true_when_row_inserted() -> None:
    mock = _mock_engine([{"id": "n1"}])
    with patch("db._engine", return_value=mock):
        assert db.claim_notification("uid", "owner/repo", "026") is True


def test_claim_notification_false_when_already_claimed() -> None:
    """An absent RETURNING row means already notified — the success path, not an error."""
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock):
        assert db.claim_notification("uid", "owner/repo", "026") is False

    sql = str(mock.begin.return_value.execute.call_args[0][0])
    assert "DO NOTHING" in sql


def test_engine_pool_is_small_for_the_shared_database():
    """The shared Postgres has ~40 usable connections: cap this process at 3 + 2."""
    with (
        patch.object(db.settings, "database_url", "postgresql://u:p@localhost:5432/db"),
        patch("db.create_engine") as create_engine,
    ):
        db._engine()
    kwargs = create_engine.call_args.kwargs
    assert kwargs["pool_size"] == 3
    assert kwargs["max_overflow"] == 2
    assert kwargs["pool_pre_ping"] is True
    # No schema pinning: the role's search_path resolves the tables.
    assert "connect_args" not in kwargs


def test_record_event_returns_the_appended_row() -> None:
    row = {"id": 7, "repo_name": "owner/repo", "kind": "question_answered", "subject": "q1"}
    mock = _mock_engine([row])
    with patch("db._engine", return_value=mock):
        result = db.record_event("uid", "owner/repo", "question_answered", "q1")

    assert result == row
    params = mock.begin.return_value.execute.call_args[0][1]
    assert params == {
        "user_id": "uid",
        "repo_name": "owner/repo",
        "kind": "question_answered",
        "subject": "q1",
    }


def test_record_event_refuses_an_unknown_kind() -> None:
    """A mistyped kind would otherwise become a permanent row no kind filter matches."""
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock), pytest.raises(ValueError, match="kind"):
        db.record_event("uid", "owner/repo", "question_answerd", "q1")  # type: ignore[arg-type]

    mock.begin.assert_not_called()


def test_list_events_reads_after_the_cursor_oldest_first() -> None:
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock):
        db.list_events("uid", 3, 100, repo_name="owner/repo", kind="note_added")

    sql = " ".join(str(mock.connect.return_value.execute.call_args[0][0]).split())
    assert "id > :after" in sql
    assert "ORDER BY id LIMIT :limit" in sql
    params = mock.connect.return_value.execute.call_args[0][1]
    assert params["after"] == 3
    assert params["repo_name"] == "owner/repo"
    assert params["kind"] == "note_added"


def test_events_migration_refuses_update_delete_and_truncate() -> None:
    """Immutability lives in the database, not only in the absence of a db.py writer."""
    from pathlib import Path

    migration = (Path(db.__file__).parent / "alembic/versions/008_events.py").read_text()
    function = re.search(
        r"CREATE OR REPLACE FUNCTION lachesis_events_immutable\(\).*?\$\$(.*?)\$\$",
        migration,
        re.DOTALL,
    )
    assert function is not None
    assert "RAISE EXCEPTION" in function.group(1)
    for timing in ("BEFORE UPDATE OR DELETE", "BEFORE TRUNCATE"):
        trigger = (
            rf"{timing} ON lachesis_events\s+"
            r"FOR EACH \w+ EXECUTE FUNCTION lachesis_events_immutable\(\)"
        )
        assert re.search(trigger, migration)


def test_record_question_stores_the_question_and_its_event_in_one_transaction() -> None:
    row = {"id": 7, "repo_name": "owner/repo", "question": "A or B?", "blocks": [160]}
    mock = _mock_engine([row])
    with patch("db._engine", return_value=mock):
        result = db.record_question(
            "uid",
            "owner/repo",
            "A or B?",
            "spec_contradiction",
            "archon",
            context="why it matters",
            options=["A", "B"],
            capability="backlog",
            blocks=[160],
        )

    assert result == row
    mock.begin.assert_called_once()
    question_call, event_call = mock.begin.return_value.execute.call_args_list
    assert "INSERT INTO lachesis_questions" in str(question_call[0][0])
    assert question_call[0][1] == {
        "user_id": "uid",
        "repo_name": "owner/repo",
        "question": "A or B?",
        "context": "why it matters",
        "options": ["A", "B"],
        "kind": "spec_contradiction",
        "asker": "archon",
        "capability": "backlog",
        "spec_change": None,
        "milestone": None,
        "issue": None,
        "blocks": [160],
    }
    assert "INSERT INTO lachesis_events" in str(event_call[0][0])
    assert event_call[0][1] == {
        "user_id": "uid",
        "repo_name": "owner/repo",
        "kind": "question_asked",
        "subject": "7",
    }


def test_record_question_refuses_an_unknown_kind() -> None:
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock), pytest.raises(ValueError, match="kind"):
        db.record_question("uid", "owner/repo", "A or B?", "clarifcation", "archon")  # type: ignore[arg-type]

    mock.begin.assert_not_called()


def test_questions_migration_allows_exactly_the_question_kinds() -> None:
    from pathlib import Path
    from typing import get_args

    migration = (Path(db.__file__).parent / "alembic/versions/009_questions.py").read_text()
    check = re.search(r"\"kind IN \((.*?)\)\"", migration.replace('"\n            "', ""))
    assert check is not None
    assert re.findall(r"'(\w+)'", check.group(1)) == list(get_args(db.QuestionKind))


def test_answer_question_records_the_answer_and_its_event_in_one_transaction() -> None:
    row = {"id": 7, "repo_name": "owner/repo", "blocks": [160, 161], "issue": 160}
    mock = _mock_engine([row])
    with patch("db._engine", return_value=mock):
        result = db.answer_question("uid", "owner/repo", 7, "Always A.", "alex", "standing_rule")

    mock.begin.assert_called_once()
    answer_call, event_call, still_blocked_call = mock.begin.return_value.execute.call_args_list
    assert "UPDATE lachesis_questions" in str(answer_call[0][0])
    assert "answered_at IS NULL" in str(answer_call[0][0])
    assert answer_call[0][1] == {
        "user_id": "uid",
        "repo_name": "owner/repo",
        "id": 7,
        "answer": "Always A.",
        "answerer": "alex",
        "outcome": "standing_rule",
    }
    assert event_call[0][1] == {
        "user_id": "uid",
        "repo_name": "owner/repo",
        "kind": "question_answered",
        "subject": "7",
    }
    assert "answered_at IS NULL" in str(still_blocked_call[0][0])
    assert still_blocked_call[0][1]["blocks"] == [160, 161]
    # The mocked engine answers every query with the same row, so issue 160 reads
    # as still blocked by another open question and 161 as released.
    assert result["released"] == [161]


def test_answer_question_refuses_a_question_that_is_not_open() -> None:
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock), pytest.raises(ValueError, match="not open"):
        db.answer_question("uid", "owner/repo", 7, "Always A.", "alex", "none")

    assert len(mock.begin.return_value.execute.call_args_list) == 1


def test_answer_question_refuses_an_unknown_outcome() -> None:
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock), pytest.raises(ValueError, match="outcome"):
        db.answer_question("uid", "owner/repo", 7, "A.", "alex", "rule")  # type: ignore[arg-type]

    mock.begin.assert_not_called()


def test_open_questions_blocking_counts_only_unanswered_questions_on_the_issue() -> None:
    mock = _mock_engine([{"id": 3}, {"id": 9}])
    with patch("db._engine", return_value=mock):
        ids = db.open_questions_blocking("uid", "owner/repo", 160)

    assert ids == [3, 9]
    (call,) = mock.connect.return_value.execute.call_args_list
    sql = str(call[0][0])
    assert "answered_at IS NULL" in sql
    assert ":issue = ANY(blocks)" in sql
    assert "ORDER BY id" in sql
    assert call[0][1] == {"user_id": "uid", "repo_name": "owner/repo", "issue": 160}


def test_list_open_questions_reads_open_questions_in_registered_repos_oldest_first() -> None:
    rows = [{"id": 3, "repo_name": "owner/repo"}, {"id": 9, "repo_name": "owner/other"}]
    mock = _mock_engine(rows)
    with patch("db._engine", return_value=mock):
        result = db.list_open_questions("uid")

    assert result == rows
    mock.begin.assert_not_called()
    (call,) = mock.connect.return_value.execute.call_args_list
    sql = str(call[0][0])
    assert "answered_at IS NULL" in sql
    assert "SELECT repo_name FROM lachesis_repos WHERE user_id = :user_id" in sql
    assert "ORDER BY created_at, id" in sql
    assert call[0][1] == {"user_id": "uid"}


def test_search_answered_questions_matches_answered_questions_by_text() -> None:
    rows = [{"id": 4, "answer": "Yes.", "outcome": "standing_rule"}]
    mock = _mock_engine(rows)
    with patch("db._engine", return_value=mock):
        result = db.search_answered_questions("uid", "owner/repo", "priority labels")

    assert result == rows
    mock.begin.assert_not_called()
    (call,) = mock.connect.return_value.execute.call_args_list
    sql = str(call[0][0])
    assert "answered_at IS NOT NULL" in sql
    assert "question || ' ' || context || ' ' || answer" in sql
    assert "plainto_tsquery('english', :query)" in sql
    assert "ORDER BY answered_at DESC, id DESC" in sql
    assert call[0][1] == {"user_id": "uid", "repo_name": "owner/repo", "query": "priority labels"}


def test_answers_migration_allows_exactly_the_outcomes() -> None:
    from pathlib import Path
    from typing import get_args

    migration = (Path(db.__file__).parent / "alembic/versions/010_question_answers.py").read_text()
    check = re.search(r"\"outcome IN \((.*?)\)\"", migration)
    assert check is not None
    assert re.findall(r"'(\w+)'", check.group(1)) == list(get_args(db.OutcomeKind))


def test_record_note_stores_the_note_and_its_event_in_one_transaction() -> None:
    row = {"id": 4, "repo_name": "owner/repo", "issue": 160, "type": "handoff"}
    mock = _mock_engine([row])
    with patch("db._engine", return_value=mock):
        result = db.record_note(
            "uid", "owner/repo", "implementer", "handoff", "Half done.", issue=160
        )

    assert result == row
    mock.begin.assert_called_once()
    note_call, event_call = mock.begin.return_value.execute.call_args_list
    assert "INSERT INTO lachesis_notes" in str(note_call[0][0])
    assert note_call[0][1] == {
        "user_id": "uid",
        "repo_name": "owner/repo",
        "issue": 160,
        "milestone": None,
        "role": "implementer",
        "type": "handoff",
        "text": "Half done.",
    }
    assert event_call[0][1] == {
        "user_id": "uid",
        "repo_name": "owner/repo",
        "kind": "note_added",
        "subject": "4",
    }


@pytest.mark.parametrize("subject", [{}, {"issue": 160, "milestone": "m1"}])
def test_record_note_refuses_anything_but_exactly_one_subject(subject: dict) -> None:
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock), pytest.raises(ValueError, match="exactly one"):
        db.record_note("uid", "owner/repo", "implementer", "handoff", "Half done.", **subject)

    mock.begin.assert_not_called()


def test_record_note_refuses_an_unknown_type() -> None:
    mock = _mock_engine([])
    with patch("db._engine", return_value=mock), pytest.raises(ValueError, match="type"):
        db.record_note("uid", "owner/repo", "implementer", "handof", "x", issue=1)  # type: ignore[arg-type]

    mock.begin.assert_not_called()


def test_list_notes_reads_a_milestones_notes_oldest_first() -> None:
    mock = _mock_engine([{"id": 1}, {"id": 2}])
    with patch("db._engine", return_value=mock):
        notes = db.list_notes("uid", "owner/repo", milestone="m1")

    assert notes == [{"id": 1}, {"id": 2}]
    (call,) = mock.connect.return_value.execute.call_args_list
    assert "(issue = :issue OR milestone = :milestone)" in str(call[0][0])
    assert "ORDER BY id" in str(call[0][0])
    assert call[0][1] == {
        "user_id": "uid",
        "repo_name": "owner/repo",
        "issue": None,
        "milestone": "m1",
    }


def _notes_migration() -> str:
    from pathlib import Path

    return (Path(db.__file__).parent / "alembic/versions/011_notes.py").read_text()


def test_notes_migration_refuses_update_delete_and_truncate() -> None:
    """Scenario: Editing refused — immutability lives in the database itself."""
    migration = _notes_migration()
    function = re.search(
        r"CREATE OR REPLACE FUNCTION lachesis_notes_immutable\(\).*?\$\$(.*?)\$\$",
        migration,
        re.DOTALL,
    )
    assert function is not None
    assert "RAISE EXCEPTION" in function.group(1)
    for timing in ("BEFORE UPDATE OR DELETE", "BEFORE TRUNCATE"):
        trigger = (
            rf"{timing} ON lachesis_notes\s+"
            r"FOR EACH \w+ EXECUTE FUNCTION lachesis_notes_immutable\(\)"
        )
        assert re.search(trigger, migration)


def test_notes_migration_allows_exactly_the_note_types_on_one_subject() -> None:
    from typing import get_args

    migration = _notes_migration()
    check = re.search(r"\"type IN \((.*?)\)\"", migration)
    assert check is not None
    assert re.findall(r"'(\w+)'", check.group(1)) == list(get_args(db.NoteType))
    assert '"(issue IS NULL) <> (milestone IS NULL)"' in migration
