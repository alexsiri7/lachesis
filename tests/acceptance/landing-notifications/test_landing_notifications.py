"""Landing notifications: a push when a whole unit of work lands, once, and never in the way.

The unit of work here is a requirement file in a repository without specs. Its issues land
when GitHub reports them closed by a merged pull request; listing the requirements is the
read that brings those facts in.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

from harness import Lachesis, Notification, scenario

REPO = "owner/repo"
REQUIREMENT_PATH = "requirements/026-widget-counts.md"
CAPABILITY = "Widgets can be counted from the dashboard."
DEPLOYMENT_LOG = f"https://github.com/{REPO}/actions/runs/7"


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


async def _register(lachesis: Lachesis, **arguments: Any) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO, **arguments})
    assert not registered.is_error, registered.text


async def _configure_notifications(lachesis: Lachesis) -> None:
    configured = await lachesis.call_tool("set_ntfy_config", {"topic": "lachesis-author"})
    assert not configured.is_error, configured.text


def _requirement(lachesis: Lachesis, issues: list[int]) -> None:
    """Requirement 026, linking *issues*, is the only file on the default branch."""
    bullets = "\n".join(f"- #{number}" for number in issues)
    content = (
        "---\nid: '026'\ntitle: Widget counts\nstatus: in-progress\n---\n\n"
        f"## What\n\n{CAPABILITY}\n\n## Issues\n\n{bullets}\n"
    )
    github = lachesis.github
    github.on(
        "GET",
        f"/repos/{REPO}/contents/requirements",
        json=[{"type": "file", "name": REQUIREMENT_PATH.split("/")[-1], "path": REQUIREMENT_PATH}],
    )
    github.on(
        "GET",
        f"/repos/{REPO}/contents/{REQUIREMENT_PATH}",
        json={
            "type": "file",
            "path": REQUIREMENT_PATH,
            "sha": "f11e",
            "encoding": "base64",
            "content": base64.b64encode(content.encode()).decode(),
        },
    )
    github.on("GET", f"/repos/{REPO}/pulls", json=[])


def _open(number: int) -> dict[str, Any]:
    stamp = _stamp()
    return {
        "number": number,
        "state": "OPEN",
        "updatedAt": stamp,
        "closedAt": None,
        "labels": {"nodes": []},
        "timelineItems": {"nodes": []},
        "closedByPullRequestsReferences": {"nodes": []},
    }


def _merged(number: int, *, commit: str | None = None) -> dict[str, Any]:
    """Issue *number*, closed by its merged pull request, which *commit* carries."""
    stamp = _stamp()
    return {
        **_open(number),
        "state": "CLOSED",
        "closedAt": stamp,
        "closedByPullRequestsReferences": {
            "nodes": [
                {
                    "number": 100 + number,
                    "state": "MERGED",
                    "merged": True,
                    "updatedAt": stamp,
                    "mergedAt": stamp,
                    "mergeCommit": {"oid": commit or f"c0mm1t{number}"},
                    "isDraft": False,
                    "commits": {"nodes": []},
                }
            ]
        },
    }


def _issues(lachesis: Lachesis, *nodes: dict[str, Any]) -> None:
    lachesis.github.on(
        "POST",
        "/graphql",
        json={"data": {"repository": {f"i{node['number']}": node for node in nodes}}},
    )


def _deployments(lachesis: Lachesis, *outcomes: str) -> None:
    """The production deployments of the final merge, each ending in its outcome."""
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/deployments",
        json=[
            {"id": n, "environment": "production", "production_environment": True, "task": "deploy"}
            for n in range(1, len(outcomes) + 1)
        ],
    )
    for n, outcome in enumerate(outcomes, start=1):
        lachesis.github.on(
            "GET",
            f"/repos/{REPO}/deployments/{n}/statuses",
            json=[{"state": outcome, "log_url": DEPLOYMENT_LOG}],
        )


async def _list(lachesis: Lachesis) -> list[dict[str, Any]]:
    result = await lachesis.call_tool("list_requirements", {"repo": REPO})
    assert not result.is_error, result.text
    return result.json()


def _sent(lachesis: Lachesis) -> list[Notification]:
    return lachesis.ntfy.notifications


async def _land_widget_counts(lachesis: Lachesis) -> list[dict[str, Any]]:
    """Requirement 026's three issues all merge, and its requirements are listed."""
    _requirement(lachesis, [11, 12, 13])
    _issues(lachesis, _merged(11), _merged(12), _merged(13))
    return await _list(lachesis)


@scenario("landing-notifications", "Last issue lands")
async def test_last_issue_lands(lachesis: Lachesis) -> None:
    await _register(lachesis)
    await _configure_notifications(lachesis)
    _requirement(lachesis, [11, 12, 13])
    _issues(lachesis, _merged(11), _merged(12), _open(13))
    await _list(lachesis)
    assert _sent(lachesis) == []

    _issues(lachesis, _merged(11), _merged(12), _merged(13))
    await _list(lachesis)

    [notification] = _sent(lachesis)
    assert "Widget counts" in notification.title
    assert REPO in notification.title
    assert CAPABILITY in notification.body
    assert notification.click == f"https://github.com/{REPO}/blob/HEAD/{REQUIREMENT_PATH}"


@scenario("landing-notifications", "Partial landing")
async def test_partial_landing(lachesis: Lachesis) -> None:
    await _register(lachesis)
    await _configure_notifications(lachesis)
    _requirement(lachesis, [11, 12, 13, 14])
    _issues(lachesis, _merged(11), _merged(12), _merged(13), _open(14))

    await _list(lachesis)

    assert _sent(lachesis) == []


@scenario("landing-notifications", "Deployment signal")
async def test_deployment_signal(lachesis: Lachesis) -> None:
    await _register(lachesis, landing_signal="deployment")
    await _configure_notifications(lachesis)
    _requirement(lachesis, [11, 12, 13])
    _issues(lachesis, _merged(11), _merged(12), _merged(13, commit="f1na1"))
    lachesis.github.on("GET", f"/repos/{REPO}/deployments", json=[])

    await _list(lachesis)
    _deployments(lachesis, "pending")
    await _list(lachesis)

    assert _sent(lachesis) == []

    _deployments(lachesis, "success")
    await _list(lachesis)

    [notification] = _sent(lachesis)
    assert "Widget counts" in notification.title
    assert "Deployed to production." in notification.body


@scenario("landing-notifications", "Failed deploy")
async def test_failed_deploy(lachesis: Lachesis) -> None:
    await _register(lachesis, landing_signal="deployment")
    await _configure_notifications(lachesis)
    _deployments(lachesis, "failure")

    await _land_widget_counts(lachesis)

    [notification] = _sent(lachesis)
    assert "Widget counts" in notification.title
    assert notification.click == DEPLOYMENT_LOG
    assert notification.priority == "high"


@scenario("landing-notifications", "Redeploy")
async def test_redeploy(lachesis: Lachesis) -> None:
    await _register(lachesis, landing_signal="deployment")
    await _configure_notifications(lachesis)
    _deployments(lachesis, "success")
    await _land_widget_counts(lachesis)
    assert len(_sent(lachesis)) == 1

    _deployments(lachesis, "success", "success")
    await _list(lachesis)

    assert len(_sent(lachesis)) == 1


@scenario("landing-notifications", "Worker asks overnight")
async def test_worker_asks_overnight(lachesis: Lachesis) -> None:
    await _register(lachesis)
    await _configure_notifications(lachesis)
    github = lachesis.github
    github.on(
        "POST",
        f"/repos/{REPO}/issues/160/comments",
        json={"html_url": f"https://github.com/{REPO}/issues/160#issuecomment-1"},
        status=201,
    )
    github.on("POST", f"/repos/{REPO}/labels", json={"name": "needs-author"}, status=201)
    github.on("POST", f"/repos/{REPO}/issues/160/labels", json=[{"name": "needs-author"}])
    question = "Should widget counts include archived widgets?"

    asked = await lachesis.call_tool(
        "ask_author", {"repo": REPO, "number": 160, "question": question}
    )

    assert not asked.is_error, asked.text
    [notification] = _sent(lachesis)
    assert REPO in notification.title
    assert "#160" in notification.title
    assert question in notification.body
    assert notification.click == f"https://github.com/{REPO}/issues/160"


@scenario("landing-notifications", "Notification service down")
async def test_notification_service_down(lachesis: Lachesis) -> None:
    await _register(lachesis)
    await _configure_notifications(lachesis)
    lachesis.ntfy.unreachable()

    [requirement] = await _land_widget_counts(lachesis)
    tested = await lachesis.call_tool("send_test_notification", {})

    assert requirement["derived"]["status"] == "done"
    assert not tested.is_error, tested.text
    assert tested.json()["sent"] is False
    assert _sent(lachesis) == []


@scenario("landing-notifications", "No topic configured")
async def test_no_topic_configured(lachesis: Lachesis) -> None:
    await _register(lachesis)

    [requirement] = await _land_widget_counts(lachesis)

    assert requirement["derived"]["status"] == "done"
    assert _sent(lachesis) == []
