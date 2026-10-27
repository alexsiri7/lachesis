"""The harness's own checks: it reaches the real app over MCP and GitHub is the stub."""

from __future__ import annotations

import httpx
import pytest

from harness import FakeGitHub, FakeNtfy, Lachesis, find_matching, scenario
from harness.github import NOT_FOUND


async def test_tools_are_called_over_the_protocol(lachesis: Lachesis) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": "owner/repo"})
    listed = await lachesis.call_tool("list_repos")

    assert not registered.is_error
    assert [r["repo_name"] for r in listed.json()] == ["owner/repo"]


async def test_each_test_starts_with_nothing_registered(lachesis: Lachesis) -> None:
    assert (await lachesis.call_tool("list_repos")).json() == []


async def test_github_is_the_stub(lachesis: Lachesis) -> None:
    await lachesis.call_tool("register_repo", {"repo": "owner/repo"})
    lachesis.github.on(
        "GET",
        "/repos/owner/repo/contents/README.md",
        json={
            "type": "file",
            "path": "README.md",
            "size": 2,
            "encoding": "base64",
            "content": "aGk=",
            "download_url": "https://raw.githubusercontent.com/owner/repo/main/README.md",
        },
    )

    result = await lachesis.call_tool("read_file", {"repo": "owner/repo", "path": "README.md"})

    assert result.json()["content"] == "hi"
    assert lachesis.github.requests_to("GET", "/repos/owner/repo/contents/README.md")


async def test_a_restarted_app_answers_and_keeps_its_database(lachesis: Lachesis) -> None:
    await lachesis.call_tool("register_repo", {"repo": "owner/repo"})

    await lachesis.restart()

    listed = await lachesis.call_tool("list_repos")
    assert [r["repo_name"] for r in listed.json()] == ["owner/repo"]


async def test_unauthenticated_calls_are_refused(lachesis: Lachesis) -> None:
    async with httpx.AsyncClient() as http:
        response = await http.post(f"{lachesis.base_url}/mcp", json={})

    assert response.status_code == 401


def test_github_answers_anything_unregistered_with_not_found() -> None:
    github = FakeGitHub()
    github.on("GET", "/repos/owner/repo/contents/README.md", json={})

    response = github.handle(
        httpx.Request("GET", "https://api.github.com/repos/owner/repo/contents/missing.md")
    )

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_github_answers_in_turn_then_repeats_the_last() -> None:
    github = FakeGitHub()
    github.on_each("POST", "/repos/owner/repo/issues", json=[{"number": 1}, {"number": 2}])

    numbers = [
        github.handle(
            httpx.Request("POST", "https://api.github.com/repos/owner/repo/issues")
        ).json()["number"]
        for _ in range(3)
    ]

    assert numbers == [1, 2, 2]


def test_github_answers_each_in_turn_with_its_own_status() -> None:
    github = FakeGitHub()
    path = "/repos/owner/repo/contents/README.md"
    github.on_sequence("PUT", path, (409, {"message": "protected"}), (200, {"content": {}}))

    statuses = [
        github.handle(httpx.Request("PUT", f"https://api.github.com{path}")).status_code
        for _ in range(3)
    ]

    assert statuses == [409, 200, 200]


def test_github_picks_out_the_requests_enabling_automerge() -> None:
    github = FakeGitHub()
    for query in ("query { repository }", "mutation { enablePullRequestAutoMerge }"):
        github.handle(
            httpx.Request(
                "POST",
                "https://api.github.com/graphql",
                json={"query": query, "variables": {"pullRequestId": "PR_1"}},
            )
        )

    assert github.automerge_requests() == [
        {"query": "mutation { enablePullRequestAutoMerge }", "variables": {"pullRequestId": "PR_1"}}
    ]


def test_github_hands_requests_under_a_served_prefix_to_its_handler() -> None:
    github = FakeGitHub()
    github.on("GET", "/repos/owner/repo/contents/README.md", json={"by": "path"})
    github.serve("/repos/owner/repo/contents", lambda r: (201, {"ref": r.params.get("ref")}))

    def answer(path: str) -> httpx.Response:
        return github.handle(httpx.Request("GET", f"https://api.github.com{path}?ref=b"))

    served = answer("/repos/owner/repo/contents/README.md")
    assert (served.status_code, served.json()) == (201, {"ref": "b"})
    assert answer("/repos/owner/repo/pulls").status_code == 404


def test_github_answers_a_bytes_body_as_it_is() -> None:
    github = FakeGitHub()
    github.on("GET", "/repos/owner/repo/contents/README.md", json=b"# Read me\n")

    response = github.handle(
        httpx.Request("GET", "https://api.github.com/repos/owner/repo/contents/README.md")
    )

    assert response.content == b"# Read me\n"


def test_find_matching_searches_every_nested_dict() -> None:
    answer = {"a": {"hit": 1, "b": [{"hit": 2}, {"miss": 3}]}, "c": [[{"hit": 4}]]}

    found = find_matching(answer, lambda d: "hit" in d)

    assert [d["hit"] for d in found] == [1, 2, 4]


def test_github_answers_graphql_by_repository_and_query() -> None:
    github = FakeGitHub()
    github.on("POST", "/graphql", json={"data": "by path"})
    github.on_graphql("owner/a", "issue(", json={"data": "a issues"})
    github.on_graphql("owner/b", "issue(", json={"data": "b issues"})

    def answer(repo: str, query: str) -> object:
        owner, _, name = repo.partition("/")
        request = httpx.Request(
            "POST",
            "https://api.github.com/graphql",
            json={"query": query, "variables": {"owner": owner, "name": name}},
        )
        return github.handle(request).json()["data"]

    assert answer("owner/a", "{ issue(number: 1) { number } }") == "a issues"
    assert answer("owner/b", "{ issue(number: 1) { number } }") == "b issues"
    assert answer("owner/a", "{ pullRequest(number: 1) { number } }") == "by path"


async def test_ntfy_is_the_stub(lachesis: Lachesis) -> None:
    await lachesis.call_tool("set_ntfy_config", {"topic": "lachesis-author"})

    result = await lachesis.call_tool("send_test_notification", {})

    assert result.json()["sent"] is True
    [notification] = lachesis.ntfy.notifications
    assert notification.topic == "lachesis-author"
    assert notification.title == "Lachesis test notification"
    assert notification.tags == "white_check_mark"


def test_ntfy_refuses_every_connection_once_unreachable() -> None:
    ntfy = FakeNtfy()
    ntfy.unreachable()

    with pytest.raises(httpx.ConnectError):
        ntfy.handle(httpx.Request("POST", "https://ntfy.sh/lachesis-author", content=b"hi"))

    assert ntfy.notifications == []


async def test_oauth_clients_are_kept_in_the_database(lachesis: Lachesis) -> None:
    async with httpx.AsyncClient(base_url=lachesis.base_url) as http:
        registered = await http.post(
            "/oauth/register", json={"redirect_uris": ["http://client.test/callback"]}
        )
        authorize = await http.get(
            "/oauth/authorize",
            params={
                "client_id": registered.json()["client_id"],
                "redirect_uri": "http://client.test/callback",
                "code_challenge": "challenge",
            },
        )

    assert registered.status_code == 201
    assert authorize.status_code == 302


async def test_a_tool_error_is_reported(lachesis: Lachesis) -> None:
    await lachesis.call_tool("register_repo", {"repo": "owner/repo"})

    result = await lachesis.call_tool("read_file", {"repo": "owner/repo", "path": "missing.md"})

    assert result.is_error
    assert "missing.md" in result.text


def test_a_scenario_is_named_positionally() -> None:
    with pytest.raises(TypeError):
        scenario(capability="delivery", title="After a merge")  # type: ignore[misc]
