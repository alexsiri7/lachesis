"""Runs the real Lachesis ASGI app on a local port against a disposable Postgres.

GitHub and ntfy.sh are stubbed.
"""

from __future__ import annotations

import importlib
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

import respx
import sqlalchemy
import uvicorn

from harness.github import FakeGitHub
from harness.ntfy import FakeNtfy

APP_DIR = Path(__file__).resolve().parents[3] / "mcp"

# Every table in it is emptied before each test, so never point it at a database you keep.
DATABASE_URL_VAR = "ACCEPTANCE_DATABASE_URL"

USER_ID = "acceptance-user"
USER_EMAIL = "acceptance@example.com"

# Fixed so nothing from the developer's shell or .env (a real PAT, a real database) leaks in.
APP_ENV = {
    "SECRET_KEY": "acceptance-secret",
    "ALLOWED_EMAIL": USER_EMAIL,
    "GITHUB_PAT": "ghp_acceptance",
    "GOOGLE_CLIENT_ID": "acceptance-client-id",
    "GOOGLE_CLIENT_SECRET": "acceptance-client-secret",
    "MCP_ALLOWED_HOSTS": "",
    "SENTRY_DSN": "",
}


@dataclass(frozen=True)
class RunningApp:
    base_url: str
    token: str
    github: FakeGitHub
    ntfy: FakeNtfy
    database: Database
    restart: Callable[[], None]


class Database:
    """The app's own schema, migrated with its own migrations, emptied between tests."""

    def __init__(self, url: str) -> None:
        self._engine = sqlalchemy.create_engine(
            url.replace("postgresql://", "postgresql+psycopg2://", 1)
        )

    def reset(self) -> None:
        """Empty every table but the signed-in user's."""
        with self._engine.begin() as conn:
            tables = conn.execute(
                sqlalchemy.text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
                    " AND tablename LIKE 'lachesis\\_%' AND tablename <> 'lachesis_users'"
                )
            ).scalars()
            # The append-only tables refuse TRUNCATE by trigger; replica mode skips triggers.
            conn.execute(sqlalchemy.text("SET LOCAL session_replication_role = replica"))
            conn.execute(sqlalchemy.text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))

    def dispose(self) -> None:
        self._engine.dispose()


def _database_url() -> str:
    url = os.environ.get(DATABASE_URL_VAR)
    if not url:
        raise RuntimeError(f"{DATABASE_URL_VAR} must name a disposable Postgres database")
    return url


def _migrate(env: dict[str, str]) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=APP_DIR,
        env={**os.environ, **env},
        check=True,
        capture_output=True,
    )


def _forget_app_modules() -> None:
    """Drop every module loaded from the app, so the next import runs it from scratch."""
    for name, module in list(sys.modules.items()):
        if Path(getattr(module, "__file__", None) or "/").is_relative_to(APP_DIR):
            del sys.modules[name]


@contextmanager
def _serve(sock: socket.socket) -> Iterator[None]:
    """Run the app's ASGI server on *sock* until the block exits."""
    main = importlib.import_module("main")
    server = uvicorn.Server(uvicorn.Config(main.app, log_level="warning"))
    # The server closes the sockets it is given on shutdown; a restart listens on sock again.
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock.dup()]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("the Lachesis app did not start")
            time.sleep(0.01)
        yield
    finally:
        server.should_exit = True
        thread.join(10)


@contextmanager
def run_app() -> Iterator[RunningApp]:
    url = _database_url()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        base_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        env = {**APP_ENV, "DATABASE_URL": url, "MCP_BASE_URL": base_url}
        _migrate(env)
        os.environ.update(env)
        sys.path.insert(0, str(APP_DIR))
        db = importlib.import_module("db")
        oauth = importlib.import_module("oauth")

        github = FakeGitHub()
        ntfy = FakeNtfy()
        database = Database(url)
        user = db.upsert_user(USER_ID, USER_EMAIL)
        token = oauth.create_jwt(USER_ID, USER_EMAIL, str(user["id"]))

        with ExitStack() as stack:
            stack.callback(database.dispose)
            router = stack.enter_context(respx.mock(assert_all_called=False))
            router.route(host="127.0.0.1").pass_through()
            router.route(host="api.github.com").mock(side_effect=github.handle)
            router.route(host="ntfy.sh").mock(side_effect=ntfy.handle)
            serving = stack.enter_context(ExitStack())
            serving.enter_context(_serve(sock))

            def restart() -> None:
                """Stop the app and start a fresh process's worth of it on the same address."""
                serving.close()
                _forget_app_modules()
                serving.enter_context(_serve(sock))

            yield RunningApp(base_url, token, github, ntfy, database, restart)
