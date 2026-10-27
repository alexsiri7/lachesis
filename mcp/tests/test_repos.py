"""Tests for mcp/tools/repos.py — repo registry MCP tools."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from auth import current_db_user_id
from tools.repos import list_repos, prepare_registered_repos, register_repo, unregister_repo


@pytest.fixture()
def set_user_id():
    """Set current_db_user_id for the test, reset afterwards."""
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


@pytest.fixture(autouse=True)
def configured() -> Iterator[MagicMock]:
    """GitHub configures every repo registered, and the outcome is recorded."""
    with (
        patch("tools.repos.unattended_merges.configure", return_value={}) as configure,
        patch("tools.repos.db.record_merge_setup_refusals"),
    ):
        yield configure


@pytest.fixture()
def clear_user_id():
    """Ensure current_db_user_id returns None (its default)."""
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
    mock_reg.assert_called_once_with("test-uid", "owner/repo", None)
    assert result["repo_name"] == "owner/repo"


@pytest.mark.anyio
async def test_register_repo_records_and_returns_what_github_refused(
    set_user_id: str, configured: MagicMock
) -> None:
    refused = {"allow_auto_merge": "GitHub answered 403: Resource not accessible"}
    configured.return_value = refused
    with (
        patch("tools.repos.db.register_repo", return_value={"repo_name": "owner/repo"}),
        patch("tools.repos.db.record_merge_setup_refusals") as record,
    ):
        result = await register_repo("https://github.com/owner/repo")
    configured.assert_called_once_with("owner/repo")
    record.assert_called_once_with("test-uid", "owner/repo", refused)
    assert result["merge_setup_refusals"] == refused


@pytest.mark.anyio
async def test_register_repo_forwards_a_valid_landing_signal(set_user_id: str) -> None:
    row = {"repo_name": "owner/repo", "landing_signal": "deployment"}
    with patch("tools.repos.db.register_repo", return_value=row) as mock_reg:
        await register_repo("owner/repo", "deployment")
    mock_reg.assert_called_once_with("test-uid", "owner/repo", "deployment")


@pytest.mark.anyio
async def test_register_repo_rejects_an_unknown_landing_signal(set_user_id: str) -> None:
    with patch("tools.repos.db.register_repo") as mock_reg:
        with pytest.raises(ValueError, match="merge, deployment"):
            await register_repo("owner/repo", "production")
    mock_reg.assert_not_called()


@pytest.mark.anyio
async def test_register_repo_raises_when_no_user(clear_user_id: None) -> None:
    with pytest.raises(RuntimeError, match="No DB user ID in request context"):
        await register_repo("owner/repo")


@pytest.mark.anyio
async def test_unregister_repo_returns_confirmation(set_user_id: str) -> None:
    with patch("tools.repos.db.unregister_repo") as mock_unreg:
        result = await unregister_repo("owner/repo")
    mock_unreg.assert_called_once_with("test-uid", "owner/repo")
    assert result == {"unregistered": "owner/repo"}


@pytest.mark.anyio
async def test_unregister_repo_raises_when_no_user(clear_user_id: None) -> None:
    with pytest.raises(RuntimeError, match="No DB user ID in request context"):
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
    with pytest.raises(RuntimeError, match="No DB user ID in request context"):
        await list_repos()


@pytest.mark.anyio
async def test_list_repos_normalizes_url_format_names(set_user_id: str) -> None:
    rows = [{"id": "r1", "repo_name": "https://github.com/owner/repo", "created_at": "t"}]
    with patch("tools.repos.db.list_repos", return_value=rows):
        result = await list_repos()
    assert result[0]["repo_name"] == "owner/repo"


@pytest.mark.anyio
async def test_unregister_repo_purges_stale_url_row(set_user_id: str) -> None:
    """Passing a full URL should delete both the normalized row and the stale URL row."""
    with patch("tools.repos.db.unregister_repo") as mock_unreg:
        result = await unregister_repo("https://github.com/owner/repo")
    assert result == {"unregistered": "owner/repo"}
    assert mock_unreg.call_count == 2
    mock_unreg.assert_any_call("test-uid", "owner/repo")
    mock_unreg.assert_any_call("test-uid", "https://github.com/owner/repo")


@pytest.mark.anyio
async def test_prepare_registered_repos_carries_on_past_a_repo_that_fails(
    configured: MagicMock,
) -> None:
    rows = [
        {"user_id": "uid", "repo_name": "owner/broken"},
        {"user_id": "uid", "repo_name": "https://github.com/owner/fine"},
    ]
    configured.side_effect = [RuntimeError("boom"), {}]
    with (
        patch("tools.repos.db.list_all_repos", return_value=rows),
        patch("tools.repos.db.record_merge_setup_refusals") as record,
    ):
        await prepare_registered_repos()
    configured.assert_called_with("owner/fine")
    record.assert_called_once_with("uid", "https://github.com/owner/fine", {})
