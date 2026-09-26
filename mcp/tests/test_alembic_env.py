"""Tests for mcp/alembic/env.py — runs the real migration entrypoint the Dockerfile CMD uses."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent.parent


def test_upgrade_loads_psycopg2_for_driverless_url() -> None:
    """Nothing listens on port 1, so a psycopg2 connection error proves the DBAPI loaded."""
    env = {**os.environ, "DATABASE_URL": "postgresql://u:p@127.0.0.1:1/db"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=MCP_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "ModuleNotFoundError" not in result.stderr
    assert "psycopg2.OperationalError" in result.stderr
