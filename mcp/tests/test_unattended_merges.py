"""Unit tests for mcp/unattended_merges.py."""

from __future__ import annotations

import json

from pytest_httpx import HTTPXMock

from unattended_merges import configure

_REPO = "owner/repo"
_API = f"https://api.github.com/repos/{_REPO}"


def _settings(httpx_mock: HTTPXMock, **overrides: object) -> None:
    httpx_mock.add_response(
        method="GET",
        url=_API,
        json={
            "default_branch": "main",
            "allow_auto_merge": True,
            "delete_branch_on_merge": True,
            **overrides,
        },
    )


def _unprotected(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/branches/main/protection",
        status_code=404,
        json={"message": "Branch not protected"},
    )


def _rules(httpx_mock: HTTPXMock, rules: list[dict[str, object]]) -> None:
    httpx_mock.add_response(
        method="GET", url=f"{_API}/rules/branches/main?per_page=100", json=rules
    )


def _pull_request_rule(source_type: str, ruleset_id: int = 9) -> dict[str, object]:
    return {
        "type": "pull_request",
        "ruleset_source_type": source_type,
        "ruleset_source": "owner" if source_type == "Organization" else _REPO,
        "ruleset_id": ruleset_id,
        "parameters": {"required_approving_review_count": 1, "dismiss_stale_reviews_on_push": True},
    }


async def test_a_prepared_repo_is_left_alone(httpx_mock: HTTPXMock) -> None:
    _settings(httpx_mock)
    _unprotected(httpx_mock)
    _rules(httpx_mock, [])

    assert await configure(_REPO) == {}
    assert all(request.method == "GET" for request in httpx_mock.get_requests())


async def test_only_the_settings_that_differ_are_changed(httpx_mock: HTTPXMock) -> None:
    _settings(httpx_mock, allow_auto_merge=False)
    httpx_mock.add_response(method="PATCH", url=_API, json={})
    _unprotected(httpx_mock)
    _rules(httpx_mock, [])

    assert await configure(_REPO) == {}
    [patched] = httpx_mock.get_requests(method="PATCH")
    assert json.loads(patched.content) == {"allow_auto_merge": True}


async def test_a_repository_ruleset_stops_requiring_approval(httpx_mock: HTTPXMock) -> None:
    _settings(httpx_mock)
    _unprotected(httpx_mock)
    _rules(httpx_mock, [_pull_request_rule("Repository")])
    status_checks = {
        "type": "required_status_checks",
        "parameters": {"required_status_checks": [{"context": "tests"}]},
    }
    httpx_mock.add_response(
        method="GET",
        url=f"{_API}/rulesets/9",
        json={"id": 9, "rules": [status_checks, _pull_request_rule("Repository")]},
    )
    httpx_mock.add_response(method="PUT", url=f"{_API}/rulesets/9", json={})

    assert await configure(_REPO) == {}
    [put] = httpx_mock.get_requests(method="PUT")
    kept, reviews = json.loads(put.content)["rules"]
    assert kept == status_checks
    assert reviews["parameters"]["required_approving_review_count"] == 0
    assert reviews["parameters"]["dismiss_stale_reviews_on_push"] is True


async def test_an_organization_ruleset_is_reported_unchanged(httpx_mock: HTTPXMock) -> None:
    _settings(httpx_mock)
    _unprotected(httpx_mock)
    _rules(httpx_mock, [_pull_request_rule("Organization")])

    refused = await configure(_REPO)

    assert list(refused) == ["ruleset 9 required_approving_review_count"]
    assert "owner" in refused["ruleset 9 required_approving_review_count"]
    assert not httpx_mock.get_requests(method="PUT")


async def test_unreadable_settings_refuse_every_setting(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="GET", url=_API, status_code=404, json={"message": "Not Found"})

    refused = await configure(_REPO)

    assert "allow_auto_merge" in refused
    assert "GitHub answered 404: Not Found" in refused["allow_auto_merge"]
