"""FastMCP bootstrap for Lachesis: tool registration, OAuth routes, ASGI middleware."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import sentry_sdk
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from auth import current_user_id, resolve_user_id_from_jwt
from settings import settings
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
    description="Create a new requirement in the repo.",
)(req_tools.create_requirement)

mcp.tool(
    description="Update fields (title, why, what, status) of an existing requirement.",
)(req_tools.update_requirement)

mcp.tool(
    description=(
        "Delete a requirement. Refuses if status is 'done' — "
        "a done requirement represents implemented code."
    ),
)(req_tools.delete_requirement)

mcp.tool(
    description="Write GitHub issue numbers and titles into a requirement's Issues section.",
)(req_tools.link_issues)

mcp.tool(
    description="Create one or more GitHub issues in the repo.",
)(req_tools.create_github_issues)

# ---------------------------------------------------------------------------
# Health check route
# ---------------------------------------------------------------------------


@mcp.custom_route("/healthz", methods=["GET"])  # type: ignore[untyped-decorator]
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# ASGI middleware — bearer token auth
# ---------------------------------------------------------------------------
_RESOURCE_METADATA_URI = "{base}/.well-known/oauth-protected-resource"


def with_user_context(app: ASGIApp) -> ASGIApp:
    async def wrapper(
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("ascii", errors="replace")
        path = scope.get("path", "")

        # Public paths — skip auth
        public_prefixes = (
            "/.well-known/",
            "/oauth/",
            "/healthz",
        )
        if any(path.startswith(p) for p in public_prefixes):
            await app(scope, receive, send)
            return

        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()

        user_id = resolve_user_id_from_jwt(token) if token else None

        if user_id is None:
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
            return

        current_user_id.set(user_id)
        await app(scope, receive, send)

    return wrapper


def build_app() -> ASGIApp:
    inner = with_user_context(mcp.streamable_http_app())

    async def _app(scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/healthz":
            body = b'{"status":"ok"}'
            await send({"type": "http.response.start", "status": 200, "headers": [[b"content-type", b"application/json"]]})
            await send({"type": "http.response.body", "body": body})
            return
        await inner(scope, receive, send)

    return _app


app = build_app()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
