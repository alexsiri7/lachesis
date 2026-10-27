"""Repositories: the registry Lachesis works over, and projects started from it in one step.

A created project's repository lives only in the GitHub stub, so whatever the creation
wrote is what the stub serves back when the project is read afterwards.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

from harness import Lachesis, scenario

OWNER = "author"
PROJECT = "name-checker"
PROJECT_REPO = f"{OWNER}/{PROJECT}"
PURPOSE = "Checks whether a name is free on every registry before it is chosen."
SPECS = "openspec/specs"

REPO = "owner/widgets"
PROTECTION = f"/repos/{REPO}/branches/main/protection"
REVIEWS = f"{PROTECTION}/required_pull_request_reviews"
STATUS_CHECKS = {"strict": True, "contexts": ["tests"], "checks": [{"context": "tests"}]}


def _spec(title: str, purpose: str) -> str:
    return (
        f"# {title}\n\n## Purpose\n\n{purpose}\n\n## Requirements\n\n"
        f"### Requirement: {title} works\n\nThe system SHALL do what {title} is for.\n\n"
        f"#### Scenario: {title} used\n"
        "- GIVEN a name\n- WHEN it is checked\n- THEN it is answered\n"
    )


CAPABILITIES = {
    "lookup": _spec("Lookup", "Looks a name up on each registry."),
    "verdict": _spec("Verdict", "Says whether the name is free everywhere."),
}


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


async def _registered(lachesis: Lachesis) -> dict[str, dict[str, Any]]:
    return {row["repo_name"]: row for row in await _ok(lachesis, "list_repos", {})}


def _find(value: Any, predicate: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    """Every dict nested anywhere in *value* that satisfies *predicate*."""
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if predicate(value):
            found.append(value)
        children = list(value.values())
    elif isinstance(value, list):
        children = value
    else:
        return found
    for child in children:
        found += _find(child, predicate)
    return found


def _repos_named(dashboard: Any) -> set[str]:
    return {entry["repo"] for entry in _find(dashboard, lambda d: isinstance(d.get("repo"), str))}


def _github_creates_repositories(lachesis: Lachesis) -> None:
    """The author's account has no repository named PROJECT, and accepts every write."""
    github = lachesis.github
    github.on(
        "POST",
        "/user/repos",
        json={
            "full_name": PROJECT_REPO,
            "default_branch": "main",
            "html_url": f"https://github.com/{PROJECT_REPO}",
            "private": True,
        },
        status=201,
    )
    for capability in (*CAPABILITIES, PROJECT):
        github.on(
            "PUT",
            f"/repos/{PROJECT_REPO}/contents/{SPECS}/{capability}/spec.md",
            json={"content": {"sha": f"sha-{capability}"}},
            status=201,
        )


def _seeded(lachesis: Lachesis) -> dict[str, str]:
    """Each capability seeded on the project's default branch, with its spec as written."""
    seeded: dict[str, str] = {}
    prefix = f"/repos/{PROJECT_REPO}/contents/{SPECS}/"
    for request in lachesis.github.requests:
        if request.method != "PUT" or not request.path.startswith(prefix):
            continue
        assert request.body.get("branch", "main") == "main", request.body
        capability = request.path.removeprefix(prefix).removesuffix("/spec.md")
        seeded[capability] = base64.b64decode(request.body["content"]).decode()
    return seeded


def _github_serves_seeded_specs(lachesis: Lachesis) -> None:
    """The project's default branch holds exactly what its creation wrote there."""
    github = lachesis.github
    seeded = _seeded(lachesis)
    github.on(
        "GET",
        f"/repos/{PROJECT_REPO}/contents/{SPECS}",
        json=[{"type": "dir", "name": name, "path": f"{SPECS}/{name}"} for name in sorted(seeded)],
    )
    for name, spec in seeded.items():
        path = f"{SPECS}/{name}/spec.md"
        github.on(
            "GET",
            f"/repos/{PROJECT_REPO}/contents/{path}",
            json={
                "type": "file",
                "path": path,
                "sha": f"sha-{name}",
                "encoding": "base64",
                "content": base64.b64encode(spec.encode()).decode(),
            },
        )


def _github_accepts_a_spec_change(lachesis: Lachesis, pull: int) -> None:
    github = lachesis.github
    github.on("GET", f"/repos/{PROJECT_REPO}/git/ref/heads/main", json={"object": {"sha": "abc"}})
    github.on("POST", f"/repos/{PROJECT_REPO}/git/refs", json={"ref": "refs/heads/x"}, status=201)
    github.on(
        "POST",
        f"/repos/{PROJECT_REPO}/pulls",
        json={
            "number": pull,
            "node_id": f"PR_{pull}",
            "html_url": f"https://github.com/{PROJECT_REPO}/pull/{pull}",
        },
        status=201,
    )
    github.on(
        "GET",
        f"/repos/{PROJECT_REPO}",
        json={
            "allow_auto_merge": True,
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "delete_branch_on_merge": True,
        },
    )
    github.on("POST", f"/repos/{PROJECT_REPO}/issues/{pull}/labels", json=[{"name": "x"}])
    for label in ("spec-change", "acceptance-tests"):
        github.on("GET", f"/repos/{PROJECT_REPO}/labels/{label}", json={"name": label})
    github.on(
        "POST",
        f"/repos/{PROJECT_REPO}/issues",
        json={
            "number": pull + 1,
            "id": 1000 + pull + 1,
            "node_id": f"I_{pull + 1}",
            "title": "Acceptance tests",
            "html_url": f"https://github.com/{PROJECT_REPO}/issues/{pull + 1}",
        },
        status=201,
    )
    github.on("POST", "/graphql", json={"data": {}})


def _repository_needing_a_human(lachesis: Lachesis) -> None:
    """REPO has auto-merge off and one approving review required on main, behind tests."""
    github = lachesis.github
    github.on(
        "GET",
        f"/repos/{REPO}",
        json={
            "full_name": REPO,
            "default_branch": "main",
            "allow_auto_merge": False,
            "allow_squash_merge": True,
            "allow_merge_commit": True,
            "allow_rebase_merge": True,
            "delete_branch_on_merge": False,
        },
    )
    github.on(
        "GET",
        PROTECTION,
        json={
            "required_status_checks": STATUS_CHECKS,
            "required_pull_request_reviews": {
                "dismiss_stale_reviews": False,
                "require_code_owner_reviews": False,
                "required_approving_review_count": 1,
            },
            "enforce_admins": {"enabled": False},
        },
    )
    github.on("GET", REVIEWS, json={"required_approving_review_count": 1})
    github.on("GET", f"{PROTECTION}/required_status_checks", json=STATUS_CHECKS)


def _auto_merge_allowed(lachesis: Lachesis) -> bool:
    return any(
        (request.body or {}).get("allow_auto_merge") is True
        for request in lachesis.github.requests_to("PATCH", f"/repos/{REPO}")
    )


def _approving_review_dropped(lachesis: Lachesis) -> bool:
    github = lachesis.github
    if github.requests_to("DELETE", REVIEWS):
        return True
    if any(
        (request.body or {}).get("required_approving_review_count") == 0
        for request in github.requests_to("PATCH", REVIEWS)
    ):
        return True
    return any(
        not ((request.body or {}).get("required_pull_request_reviews") or {}).get(
            "required_approving_review_count"
        )
        for request in github.requests_to("PUT", PROTECTION)
    )


def _status_checks_untouched(lachesis: Lachesis) -> bool:
    github = lachesis.github
    checks = f"{PROTECTION}/required_status_checks"
    if any(github.requests_to(method, checks) for method in ("PATCH", "PUT", "DELETE")):
        return False
    if any(github.requests_to(method, f"{checks}/contexts") for method in ("PUT", "DELETE")):
        return False
    if github.requests_to("DELETE", PROTECTION):
        return False
    return all(
        (request.body or {}).get("required_status_checks", {}).get("contexts")
        == STATUS_CHECKS["contexts"]
        for request in github.requests_to("PUT", PROTECTION)
    )


@scenario("repositories", "Unregistered repository")
async def test_unregistered_repository(lachesis: Lachesis) -> None:
    for repo in ("owner/kept", "owner/dropped"):
        await _ok(lachesis, "register_repo", {"repo": repo})
    await _ok(lachesis, "unregister_repo", {"repo": "owner/dropped"})

    dashboard = await _ok(lachesis, "get_dashboard", {})

    named = _repos_named(dashboard)
    assert "owner/kept" in named
    assert "owner/dropped" not in named


@scenario("repositories", "Re-register")
async def test_re_register(lachesis: Lachesis) -> None:
    await _ok(lachesis, "register_repo", {"repo": REPO, "landing_signal": "deployment"})

    await _ok(lachesis, "register_repo", {"repo": REPO})

    assert (await _registered(lachesis))[REPO]["landing_signal"] == "deployment"


@scenario("repositories", "Registering a repository with auto-merge off")
async def test_registering_a_repository_with_auto_merge_off(lachesis: Lachesis) -> None:
    _repository_needing_a_human(lachesis)
    lachesis.github.on("PATCH", f"/repos/{REPO}", json={"full_name": REPO})
    lachesis.github.on("DELETE", REVIEWS, json=None, status=204)
    lachesis.github.on("PATCH", REVIEWS, json={"required_approving_review_count": 0})
    lachesis.github.on("PUT", PROTECTION, json={})

    await _ok(lachesis, "register_repo", {"repo": REPO})

    assert _auto_merge_allowed(lachesis)
    assert _approving_review_dropped(lachesis)
    assert _status_checks_untouched(lachesis)


@scenario("repositories", "Setting cannot be changed")
async def test_setting_cannot_be_changed(lachesis: Lachesis) -> None:
    _repository_needing_a_human(lachesis)
    refusal = "Resource not accessible by personal access token"
    forbidden = {"message": refusal, "status": "403"}
    lachesis.github.on("PATCH", f"/repos/{REPO}", json=forbidden, status=403)
    for method in ("DELETE", "PATCH"):
        lachesis.github.on(method, REVIEWS, json=forbidden, status=403)
    lachesis.github.on("PUT", PROTECTION, json=forbidden, status=403)

    registered = await lachesis.call_tool("register_repo", {"repo": REPO})

    assert not registered.is_error, registered.text
    assert REPO in await _registered(lachesis)
    told = registered.text.lower()
    assert "auto-merge" in told or "allow_auto_merge" in told, registered.text
    assert refusal.lower() in told, registered.text
    dashboard = await _ok(lachesis, "get_dashboard", {})
    faults = _find(
        dashboard,
        lambda d: (
            d.get("repo") == REPO
            and "fault" in json.dumps(d).lower()
            and "auto" in json.dumps(d).lower()
        ),
    )
    assert faults, dashboard


@scenario("repositories", "Create with initial capabilities")
async def test_create_with_initial_capabilities(lachesis: Lachesis) -> None:
    _github_creates_repositories(lachesis)

    await _ok(
        lachesis,
        "create_project",
        {"name": PROJECT, "purpose": PURPOSE, "capabilities": CAPABILITIES},
    )

    [created] = lachesis.github.requests_to("POST", "/user/repos")
    assert created.body["name"] == PROJECT
    assert _seeded(lachesis) == CAPABILITIES
    assert PROJECT_REPO in await _registered(lachesis)


@scenario("repositories", "Create with purpose only")
async def test_create_with_purpose_only(lachesis: Lachesis) -> None:
    _github_creates_repositories(lachesis)

    await _ok(lachesis, "create_project", {"name": PROJECT, "purpose": PURPOSE})

    [spec] = _seeded(lachesis).values()
    purpose = spec.split("## Purpose", 1)[1].split("\n## ", 1)[0]
    assert purpose.strip() == PURPOSE


@scenario("repositories", "Immediately usable")
async def test_immediately_usable(lachesis: Lachesis) -> None:
    _github_creates_repositories(lachesis)
    await _ok(
        lachesis,
        "create_project",
        {"name": PROJECT, "purpose": PURPOSE, "capabilities": CAPABILITIES},
    )
    _github_serves_seeded_specs(lachesis)

    listed = await _ok(lachesis, "list_capabilities", {"repo": PROJECT_REPO})

    assert listed["uses_specs"] is True
    assert {capability["name"] for capability in listed["capabilities"]} == set(CAPABILITIES)
    _github_accepts_a_spec_change(lachesis, pull=7)
    proposed = await _ok(
        lachesis,
        "propose_spec_change",
        {
            "repo": PROJECT_REPO,
            "title": "Check one more registry",
            "why": "Names are also taken on the new registry.",
            "capabilities": {"lookup": _spec("Lookup", "Looks a name up on every registry.")},
        },
    )
    assert proposed["number"] == 7


@scenario("repositories", "Default visibility")
async def test_default_visibility(lachesis: Lachesis) -> None:
    _github_creates_repositories(lachesis)

    await _ok(lachesis, "create_project", {"name": PROJECT, "purpose": PURPOSE})

    [created] = lachesis.github.requests_to("POST", "/user/repos")
    assert created.body["private"] is True


@scenario("repositories", "Name already taken")
async def test_name_already_taken(lachesis: Lachesis) -> None:
    lachesis.github.on(
        "POST",
        "/user/repos",
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
        status=422,
    )

    refused = await lachesis.call_tool("create_project", {"name": "filmduel", "purpose": PURPOSE})

    assert refused.is_error
    assert "taken" in refused.text
    writes = [r for r in lachesis.github.requests if r.method in ("POST", "PUT", "PATCH")]
    assert [(r.method, r.path) for r in writes] == [("POST", "/user/repos")]
    assert await _registered(lachesis) == {}


@scenario("repositories", "Invalid capability")
async def test_invalid_capability(lachesis: Lachesis) -> None:
    malformed = "# Lookup\n\nLooks names up.\n"

    refused = await lachesis.call_tool(
        "create_project",
        {"name": PROJECT, "purpose": PURPOSE, "capabilities": {"lookup": malformed}},
    )

    assert refused.is_error
    assert "missing a '## Purpose' section" in refused.text
    assert "missing a '## Requirements' section" in refused.text
    assert not lachesis.github.requests_to("POST", "/user/repos")
