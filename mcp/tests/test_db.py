"""Tests for mcp/db.py — stubs out the Supabase client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import db


@pytest.fixture(autouse=True)
def clear_client_cache():
    """Ensure lru_cache doesn't leak between tests."""
    db._client.cache_clear()
    yield
    db._client.cache_clear()


def _mock_client(rows: list[dict]) -> MagicMock:
    """Return a MagicMock Supabase client whose .execute() returns rows."""
    mock_result = MagicMock()
    mock_result.data = rows

    mock_table = MagicMock()
    mock_table.upsert.return_value = mock_table
    mock_table.select.return_value = mock_table
    mock_table.delete.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.execute.return_value = mock_result

    mock_client = MagicMock()
    mock_client.table.return_value = mock_table
    return mock_client


def test_upsert_user_returns_row(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"id": "uuid-123", "google_id": "gid", "email": "a@b.com"}
    mock = _mock_client([expected])
    with patch("db._client", return_value=mock):
        result = db.upsert_user("gid", "a@b.com")
    assert result["id"] == "uuid-123"


def test_upsert_user_raises_on_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = _mock_client([])
    with patch("db._client", return_value=mock):
        with pytest.raises(RuntimeError, match="upsert_user returned no row"):
            db.upsert_user("gid", "a@b.com")


def test_register_repo_returns_row() -> None:
    expected = {"id": "repo-uuid", "user_id": "uid", "repo_name": "owner/repo"}
    mock = _mock_client([expected])
    with patch("db._client", return_value=mock):
        result = db.register_repo("uid", "owner/repo")
    assert result["repo_name"] == "owner/repo"


def test_list_repos_returns_list() -> None:
    rows = [{"id": "r1", "repo_name": "a/b", "created_at": "2026-05-14"}]
    mock = _mock_client(rows)
    with patch("db._client", return_value=mock):
        result = db.list_repos("uid")
    assert result == rows


def test_unregister_repo_no_exception() -> None:
    mock = _mock_client([])
    with patch("db._client", return_value=mock):
        db.unregister_repo("uid", "owner/repo")  # should not raise


def test_client_raises_if_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("db.settings.supabase_url", "")
    monkeypatch.setattr("db.settings.supabase_service_role_key", "")
    with pytest.raises(RuntimeError, match="not configured"):
        db._client()
