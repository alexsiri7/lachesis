"""Audits: recorded run by run, due when the code or the auditor changes, planned as sprint work.

The tools these tests call are the contract for the implementation issues of spec change #297:

- ``report_audit_run(repo, kind, outcome, finished_at, model, issues=[], capability=None,
  tokens_in=0, tokens_out=0)`` records one run; ``outcome`` is clean, findings or failed.
- ``read_audit_history()`` returns ``{"repos": [{"repo", "audits": [{"kind", "last_run",
  "due"}]}], "due": [{"repo", "kind", "capability"}, ...]}``: ``last_run`` is null for an
  audit that never ran there, else ``{"finished_at", "outcome", "model", "issues",
  "capability"}``; ``due`` lists the due audits in order.
- ``start_audit(repo, kind)`` opens the audit issue for the next due audit of that kind there.
- ``file_audit_finding(repo, audit_issue, finding, capability, title, body)`` files one
  finding; ``finding`` is weak_test, unmet_requirement, undescribed_behaviour or
  ambiguous_wording.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from harness import Lachesis, scenario

NOW = datetime.now(UTC)
KINDS = ("architecture", "security", "tests", "requirements")


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


def _stamp(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _pull(repo: str, number: int, merged_days_ago: float) -> dict[str, Any]:
    stamp = _stamp(merged_days_ago)
    return {
        "number": number,
        "id": 5000 + number,
        "node_id": f"PR_{number}",
        "title": f"Pull {number}",
        "body": "",
        "state": "closed",
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "created_at": stamp,
        "updated_at": stamp,
        "closed_at": stamp,
        "state_reason": None,
        "pull_request": {
            "html_url": f"https://github.com/{repo}/pull/{number}",
            "merged_at": stamp,
        },
    }


def _issue(repo: str, number: int, *, labels: tuple[str, ...] = (), state: str = "open") -> dict:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": state,
        "labels": [{"name": label} for label in labels],
        "assignees": [],
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "created_at": _stamp(10),
        "updated_at": _stamp(1),
        "closed_at": _stamp(1) if state == "closed" else None,
        "state_reason": "completed" if state == "closed" else None,
    }


async def _repo(
    lachesis: Lachesis,
    repo: str,
    *,
    items: list[dict[str, Any]] = (),  # type: ignore[assignment]
    capabilities: tuple[str, ...] = (),
) -> None:
    await _ok(lachesis, "register_repo", {"repo": repo})
    lachesis.github.on("GET", f"/repos/{repo}/issues", json=list(items))
    for item in items:
        lachesis.github.on("GET", f"/repos/{repo}/issues/{item['number']}", json=item)
    lachesis.github.on("GET", f"/repos/{repo}/pulls", json=[])
    lachesis.github.on("GET", f"/repos/{repo}/contents/requirements", json=[])
    if capabilities:
        lachesis.github.on(
            "GET",
            f"/repos/{repo}/contents/openspec/specs",
            json=[
                {"type": "dir", "name": name, "path": f"openspec/specs/{name}"}
                for name in capabilities
            ],
        )


async def _auditor_model(lachesis: Lachesis) -> str:
    return (await _ok(lachesis, "route_run", {"kind": "audit"}))["model"]


async def _run(
    lachesis: Lachesis,
    repo: str,
    kind: str,
    *,
    days_ago: float,
    model: str,
    outcome: str = "clean",
    issues: list[int] = (),  # type: ignore[assignment]
    capability: str | None = None,
    tokens: int = 1000,
) -> None:
    await _ok(
        lachesis,
        "report_audit_run",
        {
            "repo": repo,
            "kind": kind,
            "outcome": outcome,
            "finished_at": _stamp(days_ago),
            "model": model,
            "issues": list(issues),
            "capability": capability,
            "tokens_in": tokens // 2,
            "tokens_out": tokens - tokens // 2,
        },
    )


async def _history(lachesis: Lachesis) -> dict[str, Any]:
    return await _ok(lachesis, "read_audit_history", {})


def _audit(history: dict[str, Any], repo: str, kind: str) -> dict[str, Any]:
    [listing] = [entry for entry in history["repos"] if entry["repo"] == repo]
    [audit] = [entry for entry in listing["audits"] if entry["kind"] == kind]
    return audit


def _due(history: dict[str, Any], kind: str) -> list[str]:
    return [entry["repo"] for entry in history["due"] if entry["kind"] == kind]


# --- Every audit run is recorded ------------------------------------------------------


@scenario("audits", "Run recorded")
async def test_run_recorded(lachesis: Lachesis) -> None:
    await _repo(lachesis, "owner/filmduel")
    model = await _auditor_model(lachesis)
    await _run(
        lachesis,
        "owner/filmduel",
        "security",
        days_ago=1,
        model=model,
        outcome="findings",
        issues=[40, 41],
    )

    last = _audit(await _history(lachesis), "owner/filmduel", "security")["last_run"]

    assert datetime.fromisoformat(last["finished_at"]) == datetime.fromisoformat(_stamp(1))
    assert last["outcome"] == "findings"
    assert sorted(last["issues"]) == [40, 41]
    assert last["model"] == model


@scenario("audits", "Failed run recorded")
async def test_failed_run_recorded(lachesis: Lachesis) -> None:
    await _repo(lachesis, "owner/reli")
    await _run(
        lachesis,
        "owner/reli",
        "tests",
        days_ago=1,
        model=await _auditor_model(lachesis),
        outcome="failed",
    )

    history = await _history(lachesis)

    audit = _audit(history, "owner/reli", "tests")
    assert audit["last_run"]["outcome"] == "failed"
    assert audit["due"] is True
    assert "owner/reli" in _due(history, "tests")


@scenario("audits", "Never audited")
async def test_never_audited(lachesis: Lachesis) -> None:
    await _repo(lachesis, "owner/kith")

    audit = _audit(await _history(lachesis), "owner/kith", "security")

    assert audit["last_run"] is None
    assert audit["due"] is True


# --- Audits fall due when the code or the auditor changes -----------------------------


@scenario("audits", "Quiet project skipped")
async def test_quiet_project_skipped(lachesis: Lachesis) -> None:
    await _repo(lachesis, "owner/kith", items=[_pull("owner/kith", 7, merged_days_ago=5)])
    await _run(lachesis, "owner/kith", "tests", days_ago=2, model=await _auditor_model(lachesis))

    history = await _history(lachesis)

    assert _audit(history, "owner/kith", "tests")["due"] is False
    assert "owner/kith" not in _due(history, "tests")


@scenario("audits", "Busiest project first")
async def test_busiest_project_first(lachesis: Lachesis) -> None:
    model = await _auditor_model(lachesis)
    reli = [_pull("owner/reli", n, merged_days_ago=1) for n in range(100, 112)]
    filmduel = [_pull("owner/filmduel", n, merged_days_ago=1) for n in range(200, 202)]
    await _repo(lachesis, "owner/filmduel", items=filmduel)
    await _repo(lachesis, "owner/reli", items=reli)
    # filmduel's last run is the older one, so only the merge count can put reli first.
    await _run(lachesis, "owner/filmduel", "tests", days_ago=9, model=model)
    await _run(lachesis, "owner/reli", "tests", days_ago=3, model=model)

    due = _due(await _history(lachesis), "tests")

    assert due.index("owner/reli") < due.index("owner/filmduel")


@scenario("audits", "New auditor model")
async def test_new_auditor_model(lachesis: Lachesis) -> None:
    repos = ("owner/kith", "owner/dibs")
    for repo in repos:
        await _repo(lachesis, repo)
        await _run(lachesis, repo, "architecture", days_ago=2, model="an-earlier-model")
    assert await _auditor_model(lachesis) != "an-earlier-model"

    due = _due(await _history(lachesis), "architecture")

    assert sorted(due) == sorted(repos)


@scenario("audits", "Security audit of a quiet project")
async def test_security_audit_of_a_quiet_project(lachesis: Lachesis) -> None:
    await _repo(lachesis, "owner/kith", items=[_pull("owner/kith", 7, merged_days_ago=90)])
    await _run(
        lachesis, "owner/kith", "security", days_ago=61, model=await _auditor_model(lachesis)
    )

    history = await _history(lachesis)

    assert _audit(history, "owner/kith", "security")["due"] is True
    assert "owner/kith" in _due(history, "security")


# --- Audits are planned into the sprint -----------------------------------------------


async def _fuel(lachesis: Lachesis) -> None:
    await _ok(
        lachesis, "register_account", {"name": "factory", "share_percent": 100, "primary": True}
    )
    await _ok(
        lachesis,
        "report_fuel",
        {
            "account": "factory",
            "weekly_used_percent": 0,
            "weekly_resets_at": (NOW + timedelta(days=3)).isoformat(),
            "overflow_spent_usd": 0,
            "overflow_cap_usd": 0,
        },
    )


def _planned_audits(sprint: dict[str, Any], repo: str, kind: str) -> list[dict[str, Any]]:
    return [
        entry for entry in sprint["plan"] if entry.get("audit") == kind and entry["repo"] == repo
    ]


@scenario("audits", "Audit in the plan")
async def test_audit_in_the_plan(lachesis: Lachesis) -> None:
    model = await _auditor_model(lachesis)
    await _repo(lachesis, "owner/reli", items=[_pull("owner/reli", 9, merged_days_ago=1)])
    for days_ago in (40, 30, 20, 10, 5):
        await _run(lachesis, "owner/reli", "security", days_ago=days_ago, model=model, tokens=30)
    await _fuel(lachesis)

    sprint = await _ok(lachesis, "read_sprint", {})

    [planned] = _planned_audits(sprint, "owner/reli", "security")
    assert planned["estimate_tokens"] == 30


@scenario("audits", "Previous audit unfinished")
async def test_previous_audit_unfinished(lachesis: Lachesis) -> None:
    # Neither repo was ever audited, so every kind is due in both; reli's last audit
    # issue is still open, dibs has none.
    await _repo(lachesis, "owner/reli", items=[_issue("owner/reli", 50, labels=("audit",))])
    await _repo(lachesis, "owner/dibs")
    lachesis.github.on("GET", "/repos/owner/dibs/labels/audit", json={"name": "audit"})
    lachesis.github.on("POST", "/repos/owner/dibs/issues", json=_issue("owner/dibs", 3), status=201)
    await _fuel(lachesis)

    offered = await lachesis.call_tool("next_issue", {"repo": "owner/reli"})
    blocked = await lachesis.call_tool("start_audit", {"repo": "owner/reli", "kind": "security"})
    control = await lachesis.call_tool("start_audit", {"repo": "owner/dibs", "kind": "security"})

    assert not control.is_error, control.text
    assert "audit" not in (offered.json() if not offered.is_error else {})
    assert blocked.is_error
    assert not lachesis.github.requests_to("POST", "/repos/owner/reli/issues")


# --- Spec audits go one capability at a time ------------------------------------------


def _opened_issues(lachesis: Lachesis, repo: str) -> list[dict[str, Any]]:
    return [
        request.body for request in lachesis.github.requests_to("POST", f"/repos/{repo}/issues")
    ]


async def _spec_repo_for_audit(lachesis: Lachesis, repo: str) -> None:
    await _repo(lachesis, repo, capabilities=("backlog", "sprints", "fuel"))
    lachesis.github.on("GET", f"/repos/{repo}/labels/audit", json={"name": "audit"})
    lachesis.github.on(
        "POST",
        f"/repos/{repo}/issues",
        json=_issue(repo, 60, labels=("audit",)),
        status=201,
    )


@scenario("audits", "Least recently audited capability")
async def test_least_recently_audited_capability(lachesis: Lachesis) -> None:
    await _spec_repo_for_audit(lachesis, "owner/lachesis")
    model = await _auditor_model(lachesis)
    for capability, days_ago in (("sprints", 9), ("fuel", 4)):
        await _run(
            lachesis,
            "owner/lachesis",
            "tests",
            days_ago=days_ago,
            model=model,
            capability=capability,
        )

    await _ok(lachesis, "start_audit", {"repo": "owner/lachesis", "kind": "tests"})

    [opened] = _opened_issues(lachesis, "owner/lachesis")
    assert "audit" in opened["labels"]
    assert "backlog" in f"{opened['title']}\n{opened.get('body', '')}"


@scenario("audits", "Audit issue content")
async def test_audit_issue_content(lachesis: Lachesis) -> None:
    await _spec_repo_for_audit(lachesis, "owner/lachesis")

    await _ok(lachesis, "start_audit", {"repo": "owner/lachesis", "kind": "requirements"})

    [opened] = _opened_issues(lachesis, "owner/lachesis")
    text = " ".join(f"{opened['title']}\n{opened.get('body', '')}".lower().split())
    assert "backlog" in text
    assert "no scenario describes" in text
    assert "does not genuinely check" in text
    assert "requirements the code does not meet" in text
    assert "more than one reading" in text


async def _clean_code_audit_text(lachesis: Lachesis) -> str:
    repo = "owner/kith"
    await _repo(lachesis, repo)
    lachesis.github.on("POST", f"/repos/{repo}/issues", json=_issue(repo, 70), status=201)
    await _ok(lachesis, "start_audit", {"repo": repo, "kind": "clean-code"})
    [opened] = _opened_issues(lachesis, repo)
    return " ".join(f"{opened['title']}\n{opened.get('body', '')}".lower().split())


@scenario("audits", "Clean code audit content")
async def test_clean_code_audit_content(lachesis: Lachesis) -> None:
    text = await _clean_code_audit_text(lachesis)
    for standard in (
        "names that reveal intent",
        "do one thing",
        "flag arguments",
        "side effects",
        "duplication",
        "comments",
    ):
        assert standard in text, standard
    assert "name the standard" in text


@scenario("audits", "Design and test standards listed")
async def test_design_and_test_standards_listed(lachesis: Lachesis) -> None:
    text = await _clean_code_audit_text(lachesis)
    for standard in (
        "single responsibility",
        "dependencies",
        "fast",
        "independent",
        "repeatable",
        "self-validating",
        "as clean as the code",
    ):
        assert standard in text, standard


@scenario("audits", "Finding names its standard")
async def test_finding_names_its_standard(lachesis: Lachesis) -> None:
    text = await _clean_code_audit_text(lachesis)
    assert "name the standard" in text
    assert "where" in text


# --- Findings are filed, not fixed; decisions go to the author ------------------------


async def _file_finding(lachesis: Lachesis, finding: str, title: str) -> dict[str, Any]:
    repo = "owner/lachesis"
    await _spec_repo_for_audit(lachesis, repo)
    for label in ("spec-gap", "acceptance-tests", "needs-author"):
        lachesis.github.on("GET", f"/repos/{repo}/labels/{label}", json={"name": label})
    lachesis.github.on("POST", f"/repos/{repo}/issues/61/comments", json={"id": 1}, status=201)
    lachesis.github.on("POST", f"/repos/{repo}/issues/61/labels", json=[], status=200)
    lachesis.github.on("POST", f"/repos/{repo}/issues", json=_issue(repo, 61), status=201)
    await _ok(
        lachesis,
        "file_audit_finding",
        {
            "repo": repo,
            "audit_issue": 60,
            "finding": finding,
            "capability": "backlog",
            "title": title,
            "body": "Found while auditing backlog.",
        },
    )
    [opened] = _opened_issues(lachesis, repo)
    return opened


@scenario("audits", "Weak test found")
async def test_weak_test_found(lachesis: Lachesis) -> None:
    opened = await _file_finding(
        lachesis, "weak_test", "Ordered backlog test never checks the order"
    )

    assert "spec-gap" in opened["labels"]
    assert "acceptance-tests" in opened["labels"]
    assert "Capability: backlog" in opened["body"]


@scenario("audits", "Undescribed behaviour")
async def test_undescribed_behaviour(lachesis: Lachesis) -> None:
    opened = await _file_finding(
        lachesis, "undescribed_behaviour", "Failed writes are retried three times"
    )

    assert "spec-gap" in opened["labels"]
    labelled = [
        label
        for request in lachesis.github.requests_to("POST", "/repos/owner/lachesis/issues/61/labels")
        for label in (request.body or {}).get("labels", [])
    ]
    waiting = "needs-author" in opened["labels"] or "needs-author" in labelled
    assert waiting
    asked = " ".join(
        (request.body or {}).get("body", "")
        for request in lachesis.github.requests_to(
            "POST", "/repos/owner/lachesis/issues/61/comments"
        )
    ).lower()
    question = f"{opened['body']}\n{asked}".lower()
    assert "keep" in question
    assert "remove" in question


# --- Audit history is visible ---------------------------------------------------------


def _find(value: Any, predicate: Any) -> list[dict[str, Any]]:
    found = []
    if isinstance(value, dict):
        if predicate(value):
            found.append(value)
        for child in value.values():
            found += _find(child, predicate)
    elif isinstance(value, list):
        for child in value:
            found += _find(child, predicate)
    return found


@scenario("audits", "History on the dashboard")
async def test_history_on_the_dashboard(lachesis: Lachesis) -> None:
    repo = "owner/filmduel"
    await _repo(
        lachesis,
        repo,
        items=[_issue(repo, 40), _issue(repo, 41, state="closed")],
    )
    await _run(
        lachesis,
        repo,
        "security",
        days_ago=3,
        model=await _auditor_model(lachesis),
        outcome="findings",
        issues=[40, 41],
    )

    dashboard = await _ok(lachesis, "get_dashboard", {})

    [listing] = [entry for entry in dashboard["repos"] if entry["repo"] == repo]
    [security] = _find(listing, lambda d: d.get("kind") == "security" and "last_run" in d)
    last = datetime.fromisoformat(security["last_run"]["finished_at"])
    assert last == datetime.fromisoformat(_stamp(3))
    assert security["open_findings"] == [40]


@scenario("audits", "Capability never audited")
async def test_capability_never_audited(lachesis: Lachesis) -> None:
    repo = "owner/lachesis"
    await _repo(lachesis, repo, capabilities=("backlog",))

    dashboard = await _ok(lachesis, "get_dashboard", {})

    [listing] = [entry for entry in dashboard["repos"] if entry["repo"] == repo]
    [backlog] = _find(listing, lambda d: d.get("capability") == "backlog" and "last_audited" in d)
    assert backlog["last_audited"] is None
