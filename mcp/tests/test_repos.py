"""Tests for mcp/tools/repos.py — repo registry MCP tools."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from auth import current_db_user_id
from tools.repos import list_repos, register_repo, unregister_repo


@pytest.fixture()
def set_user_id():
    """Set current_db_user_id for the test, reset afterwards."""
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


@pytest.fixture()
def clear_user_id():
    """Actively set current_db_user_id to None to isolate the test from any inherited value."""
    token = current_db_user_id.set(None)
    yield
    current_db_user_id.reset(token)


@pytest.mark.anyio
async def test_register_repo_returns_row(set_user_id: str) -> None:
    row = {
        "id": "r1",
        "user_id": "uid",
        "repo_name": "owner/repo",
        "created_at": "t",
        "updated_at": "t",
    }
    with patch("tools.repos.db.register_repo", return_value=row) as mock_reg:
        result = await register_repo("owner/repo")
    mock_reg.assert_called_once_with("test-uid", "owner/repo")
    assert result["repo_name"] == "owner/repo"


@pytest.mark.anyio
async def test_register_repo_raises_when_no_user(clear_user_id: None) -> None:
    with pytest.raises(RuntimeError, match="No authenticated Supabase user"):
        await register_repo("owner/repo")


@pytest.mark.anyio
async def test_unregister_repo_returns_confirmation(set_user_id: str) -> None:
    with patch("tools.repos.db.unregister_repo") as mock_unreg:
        result = await unregister_repo("owner/repo")
    mock_unreg.assert_called_once_with("test-uid", "owner/repo")
    assert result == {"unregistered": "owner/repo"}


@pytest.mark.anyio
async def test_unregister_repo_raises_when_no_user(clear_user_id: None) -> None:
    with pytest.raises(RuntimeError, match="No authenticated Supabase user"):
        await unregister_repo("owner/repo")


@pytest.mark.anyio
async def test_list_repos_returns_list(set_user_id: str) -> None:
    rows = [{"id": "r1", "repo_name": "a/b", "created_at": "t"}]
    with patch("tools.repos.db.list_repos", return_value=rows) as mock_list:
        result = await list_repos()
    mock_list.assert_called_once_with("test-uid")
    assert result == rows


@pytest.mark.anyio
async def test_list_repos_raises_when_no_user(clear_user_id: None) -> None:
    with pytest.raises(RuntimeError, match="No authenticated Supabase user"):
        await list_repos()
