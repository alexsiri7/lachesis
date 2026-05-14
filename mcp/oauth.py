"""OAuth 2.1 endpoints for the Lachesis MCP server.

Implements the MCP Authorization spec
(https://modelcontextprotocol.io/specification/draft/basic/authorization):

  - GET  /.well-known/oauth-protected-resource     (RFC 9728)
  - GET  /.well-known/oauth-authorization-server   (RFC 8414)
  - POST /oauth/register                            (RFC 7591)
  - GET  /oauth/authorize  → 302 to Google consent screen
  - GET  /oauth/callback   ← Google redirects back with auth code
  - POST /oauth/token      → issues HS256 JWT (and refresh token)

Routes are registered as ``@mcp.custom_route()`` on the FastMCP instance via
``register_routes(mcp_obj)`` — invoked from ``main.py`` after the FastMCP
instance is constructed (avoids circular imports).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt
from mcp.server.fastmcp import FastMCP
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

import db as _db  # Supabase persistence layer — imported at module level so a missing

# supabase package fails at startup rather than silently at login time
from oauth_state import (
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


def _base_url() -> str:
    """Public base URL of the MCP server (no trailing slash). Raises 501 if unset."""
    if not settings.mcp_base_url:
        raise HTTPException(status_code=501, detail="MCP_BASE_URL not configured")
    return settings.mcp_base_url.rstrip("/")


def _create_jwt(user_id: str, email: str | None, lachesis_uid: str | None = None) -> str:
    """Mint an HS256 JWT signed with ``settings.secret_key``.

    If *lachesis_uid* is provided it is embedded as the ``lachesis_uid`` claim.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + JWT_EXPIRY_SECONDS,
    }
    if email:
        payload["email"] = email
    if lachesis_uid:
        payload["lachesis_uid"] = lachesis_uid
    return str(jwt.encode(payload, settings.secret_key, algorithm="HS256"))


def _pkce_s256(verifier: str) -> str:
    """Compute ``BASE64URL(SHA256(verifier))`` with no padding (RFC 7636 §4.6)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _issue_token_response(
    user_id: str, email: str | None, client_id: str, scope: str, lachesis_uid: str | None = None
) -> JSONResponse:
    """RFC 6749 token response with a fresh access_token + rotated refresh_token.

    *lachesis_uid* is forwarded into the JWT if present.
    """
    if not settings.secret_key:
        raise HTTPException(status_code=501, detail="SECRET_KEY not configured")

    access_token = _create_jwt(user_id, email, lachesis_uid)
    refresh_token = secrets.token_urlsafe(32)
    cleanup_and_store(
        refresh_tokens,
        refresh_token,
        {
            "user_id": user_id,
            "email": email,
            "lachesis_uid": lachesis_uid,
            "client_id": client_id,
            "scope": scope,
            "expires_at": datetime.now(UTC) + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS),
        },
    )
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": JWT_EXPIRY_SECONDS,
            "refresh_token": refresh_token,
            "scope": scope,
        }
    )


async def _exchange_google_code(code: str, redirect_uri: str) -> dict[str, Any] | None:
    """Exchange a Google auth code for tokens. Returns {id, email} or None."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                logger.warning(
                    "MCP OAuth: Google token exchange returned %s: %s — %s",
                    resp.status_code,
                    err_body.get("error", "unknown"),
                    err_body.get("error_description", ""),
                )
            except ValueError:
                logger.warning(
                    "MCP OAuth: Google token exchange returned %s (non-JSON body)", resp.status_code
                )
            return None
        payload = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("MCP OAuth: Google token exchange failed: %s", exc)
        return None
    except ValueError as exc:
        logger.warning("MCP OAuth: Google token exchange returned non-JSON: %s", exc)
        return None
    # Decode id_token to get email without another round-trip
    id_token_raw = payload.get("id_token", "")
    if not id_token_raw:
        logger.warning("MCP OAuth: no id_token in Google response")
        return None
    try:
        # Decode without verification — we just exchanged the code server-side
        claims = jwt.get_unverified_claims(id_token_raw)
    except JWTError as exc:
        logger.warning("MCP OAuth: failed to decode id_token: %s", exc)
        return None
    email = claims.get("email")
    user_id = claims.get("sub")
    email_verified = claims.get("email_verified")
    if not email or not user_id:
        logger.warning("MCP OAuth: missing email or sub in id_token claims")
        return None
    if not email_verified:
        logger.warning("MCP OAuth: email not verified by Google for email=%s", email)
        return None
    if settings.allowed_email and email != settings.allowed_email:
        logger.warning("MCP OAuth: rejected email=%s (not in allowlist)", email)
        return None
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
        except Exception:
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
        cleanup_and_store(registered_clients, client_id, client)
        logger.info(
            "MCP OAuth: registered client %s (%s)",
            client_id,
            client["client_name"],
        )
        return JSONResponse(client, status_code=201)

    # -----------------------------------------------------------------------
    # Authorization endpoint — redirects to Google consent screen
    # -----------------------------------------------------------------------
    @mcp_obj.custom_route("/oauth/authorize", methods=["GET"])  # type: ignore[untyped-decorator]
    async def oauth_authorize(request: Request) -> Response:
        if not settings.secret_key:
            raise HTTPException(status_code=501, detail="SECRET_KEY not configured")
        if not settings.google_client_id:
            raise HTTPException(status_code=501, detail="GOOGLE_CLIENT_ID not configured")
        if not settings.google_client_secret:
            raise HTTPException(status_code=501, detail="GOOGLE_CLIENT_SECRET not configured")

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
        logger.info(
            "MCP OAuth: authorize → Google consent for client %s (flow %s…)",
            client_id,
            flow_key[:12],
        )
        google_params = {
            "client_id": settings.google_client_id,
            "redirect_uri": f"{_base_url()}/oauth/callback",
            "response_type": "code",
            "scope": "openid email",
            "state": flow_key,
            "access_type": "offline",
            "prompt": "consent",
        }
        google_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(google_params)}"
        return RedirectResponse(url=google_url, status_code=302)

    # -----------------------------------------------------------------------
    # OAuth callback — Google redirects here after user consent
    # -----------------------------------------------------------------------
    @mcp_obj.custom_route("/oauth/callback", methods=["GET"])  # type: ignore[untyped-decorator]
    async def oauth_callback(request: Request) -> Response:
        qp = request.query_params
        code = qp.get("code", "")
        state = qp.get("state", "")  # this is our flow_key
        error = qp.get("error", "")

        if error:
            logger.warning("MCP OAuth: Google returned error=%s", error)
            raise HTTPException(status_code=400, detail=f"Google OAuth error: {error}")
        if not code or not state:
            raise HTTPException(status_code=400, detail="code and state required")

        session = cleanup_and_pop(oauth_sessions, state)
        if not session:
            raise HTTPException(status_code=400, detail="Invalid or expired state")

        callback_uri = f"{_base_url()}/oauth/callback"
        user_data = await _exchange_google_code(code, callback_uri)
        if user_data is None:
            raise HTTPException(
                status_code=403,
                detail="Google token exchange or email verification failed",
            )

        user_id = user_data["id"]
        email = user_data["email"]

        # Upsert user into Supabase, capture DB UUID for JWT
        lachesis_uid: str | None = None
        try:
            db_user = _db.upsert_user(user_id, email)
            lachesis_uid = str(db_user["id"])
            logger.info("MCP OAuth: upserted lachesis_user id=%s", lachesis_uid)
        except Exception as exc:
            logger.warning(
                "MCP OAuth: upsert_user failed for google_id=%s: %s",
                user_id,
                exc,
                exc_info=True,  # full traceback for post-hoc diagnosis
            )
            # Non-fatal: proceed without lachesis_uid; JWT will lack the claim

        lachesis_auth_code = secrets.token_urlsafe(32)
        cleanup_and_store(
            auth_codes,
            lachesis_auth_code,
            {
                "user_id": str(user_id),
                "email": email,
                "lachesis_uid": lachesis_uid,
                "code_challenge": session["code_challenge"],
                "code_challenge_method": session["code_challenge_method"],
                "redirect_uri": session["redirect_uri"],
                "scope": session.get("scope", "mcp"),
                "client_id": session["client_id"],
                "expires_at": datetime.now(UTC) + timedelta(seconds=AUTH_CODE_TTL_SECONDS),
            },
        )

        params = {"code": lachesis_auth_code, "state": session["client_state"]}
        redirect_url = f"{session['redirect_uri']}?{urlencode(params)}"
        logger.info(
            "MCP OAuth: callback complete for client %s → %s",
            session["client_id"],
            session["redirect_uri"],
        )
        return RedirectResponse(url=redirect_url, status_code=302)

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
                lachesis_uid=session.get("lachesis_uid"),
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

        session_client_id = session.get("client_id", "")
        if client_id and client_id != session_client_id:
            raise HTTPException(status_code=400, detail="client_id mismatch")

        return _issue_token_response(
            session["user_id"],
            session.get("email"),
            session_client_id,
            session.get("scope", "mcp"),
            lachesis_uid=session.get("lachesis_uid"),
        )
