"""Tests for the /healthz endpoint in mcp/main.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import main  # FastMCP ASGI app


@pytest.fixture()
def client():
    return TestClient(main.app)


def test_healthz_ok(client: TestClient) -> None:
    """Returns 200 and db=ok when ping succeeds."""
    with patch("main._db.ping"):  # ping returns None (success)
        resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["db"] == "ok"


def test_healthz_db_failure(client: TestClient) -> None:
    """Returns 503 and db=<error> when ping raises."""
    with patch("main._db.ping", side_effect=Exception("connection refused")):
        resp = client.get("/healthz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["ok"] is False
    assert "connection refused" in body["db"]


def test_ping(client: TestClient) -> None:
    """Returns 200 always — no DB interaction."""
    resp = client.get("/ping")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_ping_no_auth_required(client: TestClient) -> None:
    """Liveness probe must succeed even with an invalid/missing bearer token."""
    resp = client.get("/ping", headers={"Authorization": "Bearer invalid-token"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
