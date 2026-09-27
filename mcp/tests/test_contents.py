"""Tests for the repository-contents capability (mcp/tools/contents.py)."""

from __future__ import annotations

import base64
from unittest.mock import patch

import pytest
from pytest_httpx import HTTPXMock

import main
from auth import current_db_user_id
from tools.contents import MAX_READ_BYTES, list_directory, read_file

_REPO = "alexsiri7/lachesis"
_CONTENTS = f"https://api.github.com/repos/{_REPO}/contents"
_REGISTERED = [{"id": "r1", "repo_name": _REPO, "created_at": "t"}]


@pytest.fixture(autouse=True)
def _registered_repo():
    token = current_db_user_id.set("test-uid")
    with patch("tools.requirements.db.list_repos", return_value=_REGISTERED):
        yield
    current_db_user_id.reset(token)


def _file(path: str, data: bytes) -> dict:
    return {
        "type": "file",
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "size": len(data),
        "encoding": "base64",
        "content": base64.b64encode(data).decode("ascii"),
    }


def _entry(path: str, kind: str) -> dict:
    return {"type": kind, "name": path.rsplit("/", 1)[-1], "path": path}


# ---------------------------------------------------------------------------
# Spec scenarios
# ---------------------------------------------------------------------------


async def test_reading_source_for_an_audit(httpx_mock: HTTPXMock) -> None:
    source = b"def backlog():\n    return []\n"
    httpx_mock.add_response(url=f"{_CONTENTS}/src/backlog.py", json=_file("src/backlog.py", source))

    result = await read_file("lachesis", "src/backlog.py")

    assert result == {
        "repo": _REPO,
        "path": "src/backlog.py",
        "ref": None,
        "size": len(source),
        "truncated": False,
        "content": source.decode(),
    }


async def test_reading_a_pull_requests_version(httpx_mock: HTTPXMock) -> None:
    changed = b"def backlog():\n    return ['changed']\n"
    httpx_mock.add_response(
        url=f"{_CONTENTS}/src/backlog.py?ref=feature%2Fbacklog",
        json=_file("src/backlog.py", changed),
    )

    result = await read_file(_REPO, "src/backlog.py", ref="feature/backlog")

    assert result["ref"] == "feature/backlog"
    assert result["content"] == changed.decode()


async def test_finding_the_tests(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{_CONTENTS}/tests",
        json=[
            _entry("tests/test_backlog.py", "file"),
            _entry("tests/fixtures", "dir"),
        ],
    )

    result = await list_directory(_REPO, "tests")

    assert result == {
        "repo": _REPO,
        "path": "tests",
        "ref": None,
        "entries": [
            {"name": "test_backlog.py", "path": "tests/test_backlog.py", "kind": "file"},
            {"name": "fixtures", "path": "tests/fixtures", "kind": "dir"},
        ],
    }


@pytest.mark.parametrize(
    "call",
    [
        lambda: read_file("someone/else", "src/backlog.py"),
        lambda: list_directory("someone/else", "tests"),
    ],
    ids=["read_file", "list_directory"],
)
async def test_unregistered_repository(call, httpx_mock: HTTPXMock) -> None:
    with pytest.raises(ValueError, match="'someone/else' is not registered"):
        await call()
    assert httpx_mock.get_requests() == []


# ---------------------------------------------------------------------------
# Size, binary and path handling
# ---------------------------------------------------------------------------


async def test_read_file_past_the_limit_returns_the_start_and_the_full_size(
    httpx_mock: HTTPXMock,
) -> None:
    data = b"a" * (MAX_READ_BYTES + 10)
    httpx_mock.add_response(url=f"{_CONTENTS}/big.txt", json=_file("big.txt", data))

    result = await read_file(_REPO, "big.txt")

    assert result["truncated"] is True
    assert result["size"] == MAX_READ_BYTES + 10
    assert result["content"] == "a" * MAX_READ_BYTES


async def test_read_file_truncated_mid_character_drops_the_partial_character(
    httpx_mock: HTTPXMock,
) -> None:
    data = b"a" * (MAX_READ_BYTES - 1) + "€".encode() * 3
    httpx_mock.add_response(url=f"{_CONTENTS}/euro.txt", json=_file("euro.txt", data))

    result = await read_file(_REPO, "euro.txt")

    assert result["truncated"] is True
    assert result["content"] == "a" * (MAX_READ_BYTES - 1)


async def test_read_file_too_large_to_inline_fetches_the_raw_bytes(httpx_mock: HTTPXMock) -> None:
    data = "é".encode() * MAX_READ_BYTES
    httpx_mock.add_response(
        url=f"{_CONTENTS}/huge.txt",
        match_headers={"Accept": "application/vnd.github+json"},
        json={**_file("huge.txt", data), "encoding": "none", "content": ""},
    )
    httpx_mock.add_response(
        url=f"{_CONTENTS}/huge.txt",
        match_headers={"Accept": "application/vnd.github.raw+json"},
        content=data,
    )

    result = await read_file(_REPO, "huge.txt")

    assert result["truncated"] is True
    assert result["size"] == len(data)
    assert result["content"] == "é" * (MAX_READ_BYTES // 2)


async def test_read_file_returns_size_and_type_of_a_binary_file(httpx_mock: HTTPXMock) -> None:
    data = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"
    httpx_mock.add_response(url=f"{_CONTENTS}/logo.png", json=_file("logo.png", data))

    result = await read_file(_REPO, "logo.png")

    assert result == {
        "repo": _REPO,
        "path": "logo.png",
        "ref": None,
        "size": len(data),
        "binary": True,
        "type": "image/png",
    }


async def test_read_file_raises_when_missing(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=f"{_CONTENTS}/nope.py?ref=v1", status_code=404)
    with pytest.raises(FileNotFoundError, match="'nope.py' not found .* at 'v1'"):
        await read_file(_REPO, "nope.py", ref="v1")


async def test_read_file_refuses_a_directory(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=f"{_CONTENTS}/src", json=[_entry("src/a.py", "file")])
    with pytest.raises(ValueError, match="not a file"):
        await read_file(_REPO, "src")


async def test_list_directory_reports_symlinks_and_submodules_as_read_file_sees_them(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=f"{_CONTENTS}/vendor",
        json=[_entry("vendor/latest", "symlink"), _entry("vendor/lib", "submodule")],
    )
    httpx_mock.add_response(url=f"{_CONTENTS}/vendor/lib", json=_entry("vendor/lib", "submodule"))

    listing = await list_directory(_REPO, "vendor")

    assert [e["kind"] for e in listing["entries"]] == ["symlink", "submodule"]
    with pytest.raises(ValueError, match="not a file"):
        await read_file(_REPO, "vendor/lib")


async def test_list_directory_with_empty_path_lists_the_root(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=f"{_CONTENTS}/?ref=abc123", json=[_entry("README.md", "file")])

    result = await list_directory(_REPO, ref="abc123")

    assert result["entries"] == [{"name": "README.md", "path": "README.md", "kind": "file"}]


async def test_list_directory_refuses_a_file(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=f"{_CONTENTS}/README.md", json=_file("README.md", b"hi"))
    with pytest.raises(ValueError, match="use read_file"):
        await list_directory(_REPO, "README.md")


async def test_contents_tools_are_registered_read_only() -> None:
    tools = {tool.name: tool for tool in await main.mcp.list_tools()}
    for name in ("read_file", "list_directory"):
        assert tools[name].annotations.readOnlyHint is True
