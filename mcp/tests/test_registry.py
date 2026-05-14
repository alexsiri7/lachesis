"""Tests for mcp/tools/registry.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools import registry


async def test_register_repo_returns_db_row() -> None:
    expected = {"id": "r1", "user_id": "uid", "repo_name": "owner/repo", "created_at": "2026-05-14"}
    with patch.object(registry, "current_db_user_id") as mock_ctx, patch("db.register_repo", return_value=expected) as mock_db:
        mock_ctx.get.return_value = "uid"
        result = await registry.register_repo("owner/repo")
    mock_db.assert_called_once_with("uid", "owner/repo")
    assert result["repo_name"] == "owner/repo"


async def test_register_repo_raises_if_unauthenticated() -> None:
    with patch.object(registry, "current_db_user_id") as mock_ctx:
        mock_ctx.get.return_value = None
        with pytest.raises(ValueError, match="not authenticated"):
            await registry.register_repo("owner/repo")


async def test_unregister_repo_returns_confirmation() -> None:
    with patch.object(registry, "current_db_user_id") as mock_ctx, patch("db.unregister_repo") as mock_db:
        mock_ctx.get.return_value = "uid"
        result = await registry.unregister_repo("owner/repo")
    mock_db.assert_called_once_with("uid", "owner/repo")
    assert result == {"unregistered": "owner/repo"}


async def test_list_repos_returns_rows() -> None:
    rows = [{"id": "r1", "repo_name": "a/b", "created_at": "2026-05-14"}]
    with patch.object(registry, "current_db_user_id") as mock_ctx, patch("db.list_repos", return_value=rows):
        mock_ctx.get.return_value = "uid"
        result = await registry.list_repos()
    assert result == rows


async def test_list_repos_raises_if_unauthenticated() -> None:
    with patch.object(registry, "current_db_user_id") as mock_ctx:
        mock_ctx.get.return_value = None
        with pytest.raises(ValueError, match="not authenticated"):
            await registry.list_repos()
