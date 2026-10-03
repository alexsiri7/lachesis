"""Unit tests for mcp/tools/projects.py."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

from auth import current_db_user_id
from tools.projects import create_project
from tools.specs import list_capabilities, spec_problems

_NAME = "name-checker"
_REPO = f"owner/{_NAME}"
_API = f"https://api.github.com/repos/{_REPO}"
_CREATE_URL = "https://api.github.com/user/repos"
_PURPOSE = "Check whether a project name is free everywhere at once."

_LOOKUP_SPEC = """\
# Lookup

## Purpose

Names are looked up across registries.

## Requirements

### Requirement: Registries are searched

The system SHALL search every registry for a name.

#### Scenario: Taken on npm
- GIVEN a name taken on npm
- WHEN it is looked up
- THEN npm is reported as taken
"""

_REPORT_SPEC = """\
# Report

## Purpose

Results are reported in one table.

## Requirements

### Requirement: One row per registry

The system SHALL report one row per registry.

#### Scenario: Two registries
- GIVEN two registries
- WHEN a name is looked up
- THEN the table has two rows
"""


@pytest.fixture(autouse=True)
def register() -> Iterator[MagicMock]:
    token = current_db_user_id.set("test-uid")
    with patch("tools.repos.db.register_repo", return_value={}) as mock_register:
        yield mock_register
    current_db_user_id.reset(token)


def _mock_created(httpx_mock: HTTPXMock, *, private: bool = True) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_CREATE_URL,
        status_code=201,
        json={
            "full_name": _REPO,
            "html_url": f"https://github.com/{_REPO}",
            "default_branch": "main",
            "private": private,
        },
    )


def _mock_seeded(httpx_mock: HTTPXMock, capability: str, *, status_code: int = 201) -> None:
    path = f"{_API}/contents/openspec/specs/{capability}/spec.md"
    httpx_mock.add_response(method="GET", url=f"{path}?ref=main", status_code=404)
    httpx_mock.add_response(method="PUT", url=path, status_code=status_code, json={})


def _requests(httpx_mock: HTTPXMock, method: str) -> list[httpx.Request]:
    return [r for r in httpx_mock.get_requests() if r.method == method]


def _seeded(httpx_mock: HTTPXMock) -> dict[str, str]:
    seeded = {}
    for request in _requests(httpx_mock, "PUT"):
        body = json.loads(request.content)
        assert body["branch"] == "main"
        capability = request.url.path.split("/")[-2]
        seeded[capability] = base64.b64decode(body["content"]).decode()
    return seeded


async def test_create_with_initial_capabilities(httpx_mock: HTTPXMock, register: MagicMock) -> None:
    _mock_created(httpx_mock)
    _mock_seeded(httpx_mock, "lookup")
    _mock_seeded(httpx_mock, "report")

    result = await create_project(_NAME, _PURPOSE, {"lookup": _LOOKUP_SPEC, "report": _REPORT_SPEC})

    assert result == {
        "repo": _REPO,
        "url": f"https://github.com/{_REPO}",
        "private": True,
        "capabilities": ["lookup", "report"],
    }
    [create] = _requests(httpx_mock, "POST")
    assert json.loads(create.content) == {
        "name": _NAME,
        "description": _PURPOSE,
        "private": True,
        "auto_init": True,
    }
    assert _seeded(httpx_mock) == {"lookup": _LOOKUP_SPEC, "report": _REPORT_SPEC}
    register.assert_called_once_with("test-uid", _REPO, None)


async def test_create_with_purpose_only(httpx_mock: HTTPXMock) -> None:
    _mock_created(httpx_mock)
    _mock_seeded(httpx_mock, _NAME)

    result = await create_project(_NAME, _PURPOSE)

    assert result["capabilities"] == [_NAME]
    [spec] = _seeded(httpx_mock).values()
    assert spec_problems(_NAME, spec) == []
    httpx_mock.add_response(
        url=f"{_API}/contents/openspec/specs",
        json=[{"name": _NAME, "path": f"openspec/specs/{_NAME}", "type": "dir"}],
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/contents/openspec/specs/{_NAME}/spec.md",
        json={"content": base64.b64encode(spec.encode()).decode(), "sha": "s"},
    )
    assert await list_capabilities(_REPO) == {
        "repo": _REPO,
        "uses_specs": True,
        "capabilities": [{"name": _NAME, "purpose": _PURPOSE, "requirement_count": 0}],
    }


async def test_public_only_when_asked(httpx_mock: HTTPXMock) -> None:
    _mock_created(httpx_mock, private=False)
    _mock_seeded(httpx_mock, _NAME)

    result = await create_project(_NAME, _PURPOSE, public=True)

    assert result["private"] is False
    [create] = _requests(httpx_mock, "POST")
    assert json.loads(create.content)["private"] is False


async def test_name_already_taken(httpx_mock: HTTPXMock, register: MagicMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_CREATE_URL,
        status_code=422,
        json={
            "message": "Repository creation failed.",
            "errors": [
                {
                    "resource": "Repository",
                    "code": "custom",
                    "field": "name",
                    "message": "name already exists on this account",
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="'filmduel' is already taken"):
        await create_project("filmduel", _PURPOSE)

    assert len(httpx_mock.get_requests()) == 1
    register.assert_not_called()


async def test_invalid_capability(httpx_mock: HTTPXMock, register: MagicMock) -> None:
    with pytest.raises(ValueError) as refused:
        await create_project(_NAME, _PURPOSE, {"lookup": "# Lookup\n", "bad name": _REPORT_SPEC})

    message = str(refused.value)
    assert message.startswith("Project creation refused")
    assert "lookup: missing a '## Purpose' section" in message
    assert "Invalid capability name 'bad name'" in message
    assert httpx_mock.get_requests() == []
    register.assert_not_called()


async def test_project_needs_a_purpose(httpx_mock: HTTPXMock) -> None:
    with pytest.raises(ValueError, match="needs a purpose"):
        await create_project(_NAME, "  ")

    assert httpx_mock.get_requests() == []


async def test_failed_seeding_deletes_the_repository(
    httpx_mock: HTTPXMock, register: MagicMock
) -> None:
    _mock_created(httpx_mock)
    _mock_seeded(httpx_mock, _NAME, status_code=500)
    httpx_mock.add_response(method="DELETE", url=_API, status_code=204)

    with pytest.raises(RuntimeError, match="deleted again and nothing was registered"):
        await create_project(_NAME, _PURPOSE)

    assert len(_requests(httpx_mock, "DELETE")) == 1
    register.assert_not_called()


async def test_failed_registration_deletes_the_repository(
    httpx_mock: HTTPXMock, register: MagicMock
) -> None:
    _mock_created(httpx_mock)
    _mock_seeded(httpx_mock, _NAME)
    httpx_mock.add_response(method="DELETE", url=_API, status_code=204)
    register.side_effect = RuntimeError("database unreachable")

    with pytest.raises(RuntimeError, match="deleted again"):
        await create_project(_NAME, _PURPOSE)

    assert len(_requests(httpx_mock, "DELETE")) == 1


async def test_failed_rollback_says_what_is_left(httpx_mock: HTTPXMock) -> None:
    _mock_created(httpx_mock)
    _mock_seeded(httpx_mock, _NAME, status_code=500)
    httpx_mock.add_response(method="DELETE", url=_API, status_code=403)

    with pytest.raises(RuntimeError, match=f"Delete https://github.com/{_REPO} by hand"):
        await create_project(_NAME, _PURPOSE)
