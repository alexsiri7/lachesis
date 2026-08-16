"""FastMCP bootstrap for Lachesis: tool registration, OAuth routes, ASGI middleware."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime, timedelta
from typing import Any

import sentry_sdk
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

import db as _db
from auth import (
    current_db_user_id,
    current_user_id,
    decode_jwt_ignoring_expiry,
    resolve_user_id_from_jwt,
)
from settings import settings
from tools import repos as repo_tools
from tools import requirements as req_tools

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentry
# ---------------------------------------------------------------------------
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=0.1,
        environment="production",
    )

# ---------------------------------------------------------------------------
# Transport security
# ---------------------------------------------------------------------------
_allowed_hosts = [h.strip() for h in settings.mcp_allowed_hosts.split(",") if h.strip()]
if _allowed_hosts:
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
    )
else:
    _transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------
mcp: FastMCP = FastMCP(
    "Lachesis",
    stateless_http=True,
    json_response=True,
    transport_security=_transport_security,
)

# ---------------------------------------------------------------------------
# OAuth 2.1 + discovery routes
# ---------------------------------------------------------------------------
from oauth import create_jwt  # noqa: E402
from oauth import register_routes as _register_oauth_routes  # noqa: E402

_register_oauth_routes(mcp)

# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------
mcp.tool(
    description="List all requirements for a given repo.",
    annotations=ToolAnnotations(readOnlyHint=True),
)(req_tools.list_requirements)

mcp.tool(
    description="Return the full content of a single requirement file.",
    annotations=ToolAnnotations(readOnlyHint=True),
)(req_tools.get_requirement)

mcp.tool(
    description=(
        "Create a new requirement capturing intent and desired behaviour. "
        "`why` = the motivation or problem; `what` = the desired outcome, "
        "written in terms of user-visible behaviour — never implementation steps. "
        "New requirements start as 'draft' — call `create_github_issues` then "
        "`link_issues` to make it actionable and advance to 'idea'."
    ),
)(req_tools.create_requirement)

mcp.tool(
    description=(
        "Update fields of an existing requirement. "
        "`what` must describe desired behaviour, not implementation steps — "
        "those belong in GitHub issues."
    ),
)(req_tools.update_requirement)

mcp.tool(
    description=(
        "Delete a requirement. Refuses if status is 'done' — "
        "a done requirement represents implemented code."
    ),
)(req_tools.delete_requirement)

mcp.tool(
    description=(
        "Attach GitHub issue numbers and titles to a requirement's Issues section, "
        "completing the requirement → issue link. "
        "Automatically advances status from 'draft' to 'idea' when issues are first linked."
    ),
)(req_tools.link_issues)

mcp.tool(
    description=(
        "Create one or more GitHub issues capturing implementation work. "
        "Issues are the task layer — they break down *how* a requirement's "
        "desired behaviour will be built. Call `link_issues` after to attach "
        "them to the requirement."
    ),
)(req_tools.create_github_issues)

mcp.tool(
    description="Register a repo so Lachesis tracks it.",
)(repo_tools.register_repo)

mcp.tool(
    description="Unregister a repo from Lachesis tracking.",
)(repo_tools.unregister_repo)

mcp.tool(
    description="List all repos registered with Lachesis.",
    annotations=ToolAnnotations(readOnlyHint=True),
)(repo_tools.list_repos)

mcp.tool(
    description="Return cross-repo requirements summary with optional status filter"
    " (draft, idea, planned, in-progress, done).",
    annotations=ToolAnnotations(readOnlyHint=True),
)(req_tools.get_dashboard)

mcp.tool(
    description=(
        "Return the current state of a GitHub issue: "
        "title, body, state (open/closed), assignees, labels, "
        "and linked PR URL if any."
    ),
    annotations=ToolAnnotations(readOnlyHint=True),
)(req_tools.get_github_issue)

mcp.tool(
    description=(
        "Update a GitHub issue's title, body, and/or state. "
        "fields accepts: title (str), body (str), state ('open' or 'closed'). "
        "Returns the updated issue number, title, and URL."
    ),
)(req_tools.update_github_issue)

mcp.tool(
    description=(
        "Return the dependency relationships of a GitHub issue: "
        "blocked_by (issues this one depends on) and "
        "blocking (issues that depend on this one). "
        "Returns lists of issue numbers."
    ),
    annotations=ToolAnnotations(readOnlyHint=True),
)(req_tools.get_dependencies)

mcp.tool(
    description=(
        "Mark a GitHub issue as blocked by another issue. "
        "Returns the updated dependency state for the issue: "
        "blocked_by (issues this one depends on) and "
        "blocking (issues that depend on this one)."
    ),
)(req_tools.add_dependency)

mcp.tool(
    description=(
        "Return the standard Lachesis workflow. Call this at the start of a session "
        "to learn the correct order: create_requirement → create_github_issues → link_issues."
    ),
    annotations=ToolAnnotations(readOnlyHint=True),
)(req_tools.get_initial_instructions)

# ---------------------------------------------------------------------------
# Health check route
# ---------------------------------------------------------------------------


@mcp.custom_route("/healthz", methods=["GET"])  # type: ignore[untyped-decorator]
async def healthz(request: Request) -> JSONResponse:
    try:
        await asyncio.get_running_loop().run_in_executor(None, _db.ping)
        db_status = "ok"
    except Exception as exc:
        logger.warning("healthz: db ping failed: %s", exc, exc_info=True)
        db_status = str(exc)
    ok = db_status == "ok"
    return JSONResponse({"ok": ok, "db": db_status}, status_code=200 if ok else 503)


@mcp.custom_route("/ping", methods=["GET"])  # type: ignore[untyped-decorator]
async def ping(request: Request) -> JSONResponse:
    """Liveness probe for Railway — always 200 if the process is running."""
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Web UI routes
# ---------------------------------------------------------------------------
from web import web_callback, web_index, web_login, web_logout  # noqa: E402

mcp.custom_route("/web/", methods=["GET"])(web_index)
mcp.custom_route("/web/login", methods=["GET"])(web_login)
mcp.custom_route("/web/callback", methods=["GET"])(web_callback)
mcp.custom_route("/web/logout", methods=["GET"])(web_logout)


# ---------------------------------------------------------------------------
# ASGI middleware — bearer token auth
# ---------------------------------------------------------------------------
_RESOURCE_METADATA_URI = "{base}/.well-known/oauth-protected-resource"

_REFRESH_TOKEN_WARN_DAYS = 5
_startup_check_done = False


def _check_refresh_token_health() -> None:
    """Log refresh token health. Runs once on first request. Non-fatal if DB unavailable."""
    try:
        tokens = _db.list_active_refresh_tokens()
    except Exception as exc:
        logger.warning("startup token health check failed: %s", exc)
        return
    if not tokens:
        logger.warning(
            "startup check: no active refresh tokens found — "
            "MCP clients will need to re-authenticate"
        )
        return
    now = datetime.now(UTC)
    buffer = timedelta(days=_REFRESH_TOKEN_WARN_DAYS)
    expiring_soon = [
        t
        for t in tokens
        if (
            t["expires_at"].replace(tzinfo=UTC)
            if t["expires_at"].tzinfo is None
            else t["expires_at"]
        )
        - now
        < buffer
    ]
    if expiring_soon:
        logger.warning(
            "startup check: %d refresh token(s) expiring within %d days",
            len(expiring_soon),
            _REFRESH_TOKEN_WARN_DAYS,
        )
    else:
        logger.info("startup check: %d active refresh token(s), all healthy", len(tokens))


def _apply_user_context(user_id: str, email: str, lachesis_uid: str | None) -> None:
    """Set current_user_id and current_db_user_id context vars for this request."""
    current_user_id.set(user_id)
    if lachesis_uid:
        current_db_user_id.set(lachesis_uid)
    else:
        try:
            db_user = _db.upsert_user(user_id, email)
            current_db_user_id.set(str(db_user["id"]))
        except Exception:
            logger.warning(
                "middleware: upsert_user fallback failed for sub=%s", user_id, exc_info=True
            )


def with_user_context(app: ASGIApp) -> ASGIApp:
    async def wrapper(
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        global _startup_check_done

        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        # Run startup health check once on first HTTP request
        if not _startup_check_done:
            _startup_check_done = True
            _check_refresh_token_health()

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("ascii", errors="replace")
        path = scope.get("path", "")

        # Public paths — skip auth
        public_prefixes = (
            "/.well-known/",
            "/oauth/",
            "/healthz",
            "/ping",
            "/web/",
        )
        if any(path.startswith(p) for p in public_prefixes):
            await app(scope, receive, send)
            return

        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()

        resolved = resolve_user_id_from_jwt(token) if token else None

        if resolved is not None:
            # Valid token — normal path
            user_id, email, lachesis_uid = resolved
            _apply_user_context(user_id, email, lachesis_uid)
            await app(scope, receive, send)
            return

        # JWT failed validation — check if it's an expired (but otherwise valid) token
        if token:
            decoded = decode_jwt_ignoring_expiry(token)
            if decoded is not None:
                user_id, email, lachesis_uid = decoded
                # Look up a valid refresh token for this user
                try:
                    rt = _db.get_refresh_token_by_user_id(user_id)
                except Exception as exc:
                    logger.warning(
                        "middleware: refresh token lookup failed for sub=%s: %s", user_id, exc
                    )
                    rt = None
                if rt is not None:
                    _rt_token, rt_data = rt
                    if not lachesis_uid:
                        lachesis_uid = rt_data.get("lachesis_uid")
                    new_access_token = create_jwt(user_id, email, lachesis_uid)
                    _apply_user_context(user_id, email, lachesis_uid)
                    logger.info("middleware: auto-refreshed expired token for sub=%s", user_id)

                    async def _send_with_new_token(event: MutableMapping[str, Any]) -> None:
                        if event["type"] == "http.response.start":
                            headers_list = list(event.get("headers", []))
                            headers_list.append(
                                [b"x-lachesis-access-token", new_access_token.encode()]
                            )
                            event = {**event, "headers": headers_list}
                        await send(event)

                    await app(scope, receive, _send_with_new_token)
                    return
                else:
                    # Expired JWT + no valid refresh token → actionable 401
                    logger.warning(
                        "middleware: expired token with no valid refresh token for sub=%s", user_id
                    )
                    base = settings.mcp_base_url.rstrip("/")
                    resource_uri = _RESOURCE_METADATA_URI.format(base=base)
                    body = json.dumps(
                        {
                            "error": "token_expired",
                            "description": (
                                "Access token has expired and no valid refresh token was found. "
                                "Please re-authenticate via the OAuth flow."
                            ),
                        }
                    ).encode()
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 401,
                            "headers": [
                                [b"content-type", b"application/json"],
                                [
                                    b"www-authenticate",
                                    f'Bearer realm="lachesis" error="invalid_token" '
                                    f'resource_metadata="{resource_uri}"'.encode(),
                                ],
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return

        # No token or completely invalid token → generic 401
        base = settings.mcp_base_url.rstrip("/")
        resource_uri = _RESOURCE_METADATA_URI.format(base=base)
        body = b'{"error":"unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    [b"content-type", b"application/json"],
                    [
                        b"www-authenticate",
                        f'Bearer resource_metadata="{resource_uri}"'.encode(),
                    ],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return wrapper


def build_app() -> ASGIApp:
    return with_user_context(mcp.streamable_http_app())


app = build_app()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
