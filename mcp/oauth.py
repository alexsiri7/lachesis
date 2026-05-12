"""OAuth 2.1 endpoints for the Lachesis MCP server.

Implements the MCP Authorization spec
(https://modelcontextprotocol.io/specification/draft/basic/authorization):

  - GET  /.well-known/oauth-protected-resource     (RFC 9728)
  - GET  /.well-known/oauth-authorization-server   (RFC 8414)
  - POST /oauth/register                            (RFC 7591)
  - GET  /oauth/authorize  → 302 to web app /mcp-auth relay page
  - POST /oauth/code-from-session ← web app posts Google token after OAuth
  - POST /oauth/token      → issues HS256 JWT (and refresh token)

Routes are registered as ``@mcp.custom_route()`` on the FastMCP instance via
``register_routes(mcp_obj)`` — invoked from ``main.py`` after the FastMCP
instance is constructed (avoids circular imports).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from jose import jwt
from mcp.server.fastmcp import FastMCP
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from oauth_state import (
    StoreFullError,
    auth_codes,
    cleanup_and_pop,
    cleanup_and_store,
    oauth_sessions,
    refresh_tokens,
    registered_clients,
)
from settings import settings

logger = logging.getLogger(__name__)

AUTH_CODE_TTL_SECONDS = 60 * 10  # 10 minutes
SESSION_TTL_SECONDS = 60 * 10  # 10 minutes
JWT_EXPIRY_SECONDS = 60 * 60 * 24 * 7  # 7 days
REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days

# Relay origin for /mcp-auth — the web-app page that handles Google OAuth.
# Hardcoded to the production domain; move to an env var if the relay moves.
WEB_APP_ORIGIN = "https://lachesis.interstellarai.net"

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": WEB_APP_ORIGIN,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _base_url() -> str:
    """Public base URL of the MCP server (no trailing slash). Raises 501 if unset."""
    if not settings.mcp_base_url:
        raise HTTPException(status_code=501, detail="MCP_BASE_URL not configured")
    return settings.mcp_base_url.rstrip("/")


def _create_jwt(user_id: str, email: str | None) -> str:
    """Mint an HS256 JWT signed with ``settings.secret_key``."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + JWT_EXPIRY_SECONDS,
    }
    if email:
        payload["email"] = email
    return str(jwt.encode(payload, settings.secret_key, algorithm="HS256"))


def _pkce_s256(verifier: str) -> str:
    """Compute ``BASE64URL(SHA256(verifier))`` with no padding (RFC 7636 §4.6)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _issue_token_response(
    user_id: str, email: str | None, client_id: str, scope: str
) -> JSONResponse:
    """RFC 6749 token response with a fresh access_token + rotated refresh_token."""
    if not settings.secret_key:
        raise HTTPException(status_code=501, detail="SECRET_KEY not configured")

    access_token = _create_jwt(user_id, email)
    refresh_token = secrets.token_urlsafe(32)
    try:
        cleanup_and_store(
            refresh_tokens,
            refresh_token,
            {
                "user_id": user_id,
                "email": email,
                "client_id": client_id,
                "scope": scope,
                "expires_at": datetime.now(UTC) + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS),
            },
        )
    except StoreFullError:
        logger.error("MCP OAuth: refresh_tokens store full — cannot issue token")
        raise HTTPException(status_code=503, detail="server_error: token store full")
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": JWT_EXPIRY_SECONDS,
            "refresh_token": refresh_token,
            "scope": scope,
        }
    )


async def _verify_google_token(access_token: str) -> dict[str, Any] | None:
    """Verify a Google access token via the tokeninfo endpoint.

    Returns a dict with ``id`` and ``email`` on success, or ``None`` if the
    token is invalid or the email is not in the allowlist.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"access_token": access_token},
            )
        if resp.status_code != 200:
            logger.warning("MCP OAuth: Google tokeninfo returned %s", resp.status_code)
            return None
        payload = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("MCP OAuth: Google tokeninfo failed: %s", exc)
        return None
    except ValueError as exc:
        logger.warning("MCP OAuth: Google tokeninfo returned non-JSON: %s", exc)
        return None
    aud = payload.get("aud")
    if settings.google_client_id and aud != settings.google_client_id:
        logger.warning("MCP OAuth: tokeninfo aud mismatch: %s", aud)
        return None
    email = payload.get("email")
    if not email:
        logger.warning("MCP OAuth: rejected email=%s", email)
        return None
    if not settings.allowed_email or email != settings.allowed_email:
        logger.warning("MCP OAuth: rejected email=%s", email)
        return None
    user_id = payload.get("sub") or payload.get("user_id")
    return {"id": user_id, "email": email}


def register_routes(mcp_obj: FastMCP) -> None:
    """Register all OAuth + discovery routes on the given FastMCP instance.

    Called from ``main.py`` after ``mcp = FastMCP(...)`` is constructed. Using
    a function (rather than module-level decorators) avoids the ``main → oauth
    → main`` circular import that would otherwise occur.
    """

    # -----------------------------------------------------------------------
    # RFC 9728: Protected Resource Metadata
    # -----------------------------------------------------------------------
    @mcp_obj.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])  # type: ignore[untyped-decorator]
    async def protected_resource_metadata(_request: Request) -> Response:
        base = _base_url()
        return JSONResponse(
            {
                "resource": f"{base}/mcp",
                "authorization_servers": [base],
                "scopes_supported": ["mcp"],
                "bearer_methods_supported": ["header"],
            }
        )

    # -----------------------------------------------------------------------
    # RFC 8414: Authorization Server Metadata
    # -----------------------------------------------------------------------
    @mcp_obj.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])  # type: ignore[untyped-decorator]
    async def authorization_server_metadata(_request: Request) -> Response:
        base = _base_url()
        return JSONResponse(
            {
                "issuer": base,
                "authorization_endpoint": f"{base}/oauth/authorize",
                "token_endpoint": f"{base}/oauth/token",
                "registration_endpoint": f"{base}/oauth/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": ["mcp"],
            }
        )

    # -----------------------------------------------------------------------
    # RFC 7591: Dynamic Client Registration
    # -----------------------------------------------------------------------
    @mcp_obj.custom_route("/oauth/register", methods=["POST"])  # type: ignore[untyped-decorator]
    async def oauth_register(request: Request) -> Response:
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError) as exc:
            logger.debug("MCP OAuth: /register body parse error: %s", exc)
            body = {}
        if not isinstance(body, dict):
            body = {}

        client_id = uuid.uuid4().hex
        client_secret = secrets.token_urlsafe(32)
        client = {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": body.get("redirect_uris", []),
            "client_name": body.get("client_name", ""),
            "grant_types": body.get("grant_types", ["authorization_code"]),
            "response_types": body.get("response_types", ["code"]),
            "token_endpoint_auth_method": body.get(
                "token_endpoint_auth_method", "client_secret_post"
            ),
            "scope": body.get("scope", "mcp"),
        }
        try:
            cleanup_and_store(registered_clients, client_id, client)
        except StoreFullError:
            logger.error("MCP OAuth: registered_clients store full — cannot register client")
            raise HTTPException(status_code=503, detail="server_error: OAuth state store full")
        logger.info(
            "MCP OAuth: registered client %s (%s)",
            client_id,
            client["client_name"],
        )
        return JSONResponse(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uris": client["redirect_uris"],
                "client_name": client["client_name"],
                "grant_types": client["grant_types"],
                "response_types": client["response_types"],
                "token_endpoint_auth_method": client["token_endpoint_auth_method"],
                "scope": client["scope"],
            },
            status_code=201,
        )

    # -----------------------------------------------------------------------
    # Authorization endpoint — redirects to web-app OAuth relay
    # -----------------------------------------------------------------------
    @mcp_obj.custom_route("/oauth/authorize", methods=["GET"])  # type: ignore[untyped-decorator]
    async def oauth_authorize(request: Request) -> Response:
        if not settings.secret_key:
            raise HTTPException(status_code=501, detail="SECRET_KEY not configured")

        qp = request.query_params
        client_id = qp.get("client_id", "")
        redirect_uri = qp.get("redirect_uri", "")
        client_state = qp.get("state", "")
        code_challenge = qp.get("code_challenge", "")
        code_challenge_method = qp.get("code_challenge_method", "S256")
        scope = qp.get("scope", "mcp")
        response_type = qp.get("response_type", "code")

        if response_type != "code":
            raise HTTPException(status_code=400, detail="Only response_type=code is supported")
        if code_challenge_method != "S256":
            raise HTTPException(
                status_code=400,
                detail="Only code_challenge_method=S256 is supported",
            )
        if not redirect_uri:
            raise HTTPException(status_code=400, detail="redirect_uri is required")
        if not code_challenge:
            raise HTTPException(
                status_code=400, detail="code_challenge is required (PKCE required)"
            )
        registered = registered_clients.get(client_id)
        if not registered:
            raise HTTPException(
                status_code=400,
                detail="Unknown client_id — register first via POST /oauth/register",
            )
        if redirect_uri not in registered.get("redirect_uris", []):
            raise HTTPException(
                status_code=400, detail="redirect_uri not registered for this client"
            )

        flow_key = secrets.token_urlsafe(32)
        try:
            cleanup_and_store(
                oauth_sessions,
                flow_key,
                {
                    "client_state": client_state,
                    "redirect_uri": redirect_uri,
                    "code_challenge": code_challenge,
                    "code_challenge_method": code_challenge_method,
                    "client_id": client_id,
                    "scope": scope,
                    "expires_at": datetime.now(UTC) + timedelta(seconds=SESSION_TTL_SECONDS),
                },
            )
        except StoreFullError:
            logger.error("MCP OAuth: oauth_sessions store full — cannot start authorization flow")
            raise HTTPException(status_code=503, detail="server_error: OAuth state store full")
        logger.info(
            "MCP OAuth: authorize → web-app relay for client %s (flow %s…)",
            client_id,
            flow_key[:12],
        )
        return RedirectResponse(
            url=f"{WEB_APP_ORIGIN}/mcp-auth?flow={flow_key}",
            status_code=302,
        )

    # -----------------------------------------------------------------------
    # Code-from-session — called by the web-app relay after Google OAuth
    # -----------------------------------------------------------------------
    @mcp_obj.custom_route("/oauth/code-from-session", methods=["POST", "OPTIONS"])  # type: ignore[untyped-decorator]
    async def oauth_code_from_session(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(headers=_CORS_HEADERS)

        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError) as exc:
            logger.debug("MCP OAuth: /code-from-session body parse error: %s", exc)
            return JSONResponse(
                {"error": "invalid_request", "error_description": "JSON body required"},
                status_code=400,
                headers=_CORS_HEADERS,
            )

        flow_id = str(body.get("flow_id", ""))
        access_token = str(body.get("access_token", ""))

        logger.info(
            "MCP OAuth: code-from-session received flow_id=%r len=%d",
            flow_id[:12] if flow_id else "",
            len(flow_id),
        )

        if not flow_id or not access_token:
            return JSONResponse(
                {
                    "error": "invalid_request",
                    "error_description": "flow_id and access_token required",
                },
                status_code=400,
                headers=_CORS_HEADERS,
            )

        session = cleanup_and_pop(oauth_sessions, flow_id)
        if not session:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Invalid or expired flow_id"},
                status_code=400,
                headers=_CORS_HEADERS,
            )

        user_data = await _verify_google_token(access_token)
        if user_data is None:
            return JSONResponse(
                {"error": "server_error", "error_description": "Token verification failed"},
                status_code=400,
                headers=_CORS_HEADERS,
            )
        user_id = user_data.get("id")
        email = user_data.get("email")
        if not user_id:
            return JSONResponse(
                {"error": "server_error", "error_description": "No user ID in token"},
                status_code=400,
                headers=_CORS_HEADERS,
            )

        lachesis_auth_code = secrets.token_urlsafe(32)
        try:
            cleanup_and_store(
                auth_codes,
                lachesis_auth_code,
                {
                    "user_id": str(user_id),
                    "email": email,
                    "code_challenge": session["code_challenge"],
                    "code_challenge_method": session["code_challenge_method"],
                    "redirect_uri": session["redirect_uri"],
                    "scope": session.get("scope", "mcp"),
                    "client_id": session["client_id"],
                    "expires_at": datetime.now(UTC) + timedelta(seconds=AUTH_CODE_TTL_SECONDS),
                },
            )
        except StoreFullError:
            logger.error("MCP OAuth: auth_codes store full — cannot issue authorization code")
            return JSONResponse(
                {"error": "server_error", "error_description": "OAuth state store full"},
                status_code=503,
                headers=_CORS_HEADERS,
            )

        params = {"code": lachesis_auth_code, "state": session["client_state"]}
        redirect_url = f"{session['redirect_uri']}?{urlencode(params)}"
        logger.info(
            "MCP OAuth: code-from-session complete for client %s → %s",
            session["client_id"],
            session["redirect_uri"],
        )
        return JSONResponse({"redirect_url": redirect_url}, headers=_CORS_HEADERS)

    # -----------------------------------------------------------------------
    # Token endpoint
    # -----------------------------------------------------------------------
    @mcp_obj.custom_route("/oauth/token", methods=["POST"])  # type: ignore[untyped-decorator]
    async def oauth_token(request: Request) -> Response:
        if not settings.secret_key:
            raise HTTPException(status_code=501, detail="SECRET_KEY not configured")

        form = await request.form()
        grant_type = str(form.get("grant_type", ""))

        if grant_type == "refresh_token":
            refresh_token = str(form.get("refresh_token", ""))
            if not refresh_token:
                raise HTTPException(status_code=400, detail="refresh_token is required")
            session = cleanup_and_pop(refresh_tokens, refresh_token)
            if not session:
                raise HTTPException(status_code=400, detail="Invalid or expired refresh_token")
            return _issue_token_response(
                session["user_id"],
                session.get("email"),
                session["client_id"],
                session.get("scope", "mcp"),
            )

        if grant_type != "authorization_code":
            raise HTTPException(status_code=400, detail="Unsupported grant_type")

        code = str(form.get("code", ""))
        redirect_uri = str(form.get("redirect_uri", ""))
        client_id = str(form.get("client_id", ""))
        code_verifier = str(form.get("code_verifier", ""))

        session = cleanup_and_pop(auth_codes, code)
        if not session:
            raise HTTPException(status_code=400, detail="Invalid or expired authorization code")
        if redirect_uri != session["redirect_uri"]:
            raise HTTPException(status_code=400, detail="redirect_uri mismatch")

        if session.get("code_challenge_method") == "S256":
            if not code_verifier:
                raise HTTPException(status_code=400, detail="code_verifier is required")
            if _pkce_s256(code_verifier) != session["code_challenge"]:
                raise HTTPException(status_code=400, detail="PKCE verification failed")

        return _issue_token_response(
            session["user_id"],
            session.get("email"),
            client_id or session.get("client_id", ""),
            session.get("scope", "mcp"),
        )
