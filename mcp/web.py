"""Browser-facing web dashboard routes with cookie-based Google OAuth session."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from typing import Any, cast
from urllib.parse import urlencode

import frontmatter
from jinja2 import Environment, FileSystemLoader, select_autoescape
from jose import JWTError
from jose import jwt as jose_jwt
from jose.exceptions import ExpiredSignatureError
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

import db
import github
from oauth import create_jwt, exchange_google_code
from settings import settings
from status import canonical_status
from tools.specs import read_spec_progress, spec_change_status, uses_specs

logger = logging.getLogger(__name__)


def _extract_section(body: str, heading: str) -> str:
    """Return the text content of a '## {heading}' section from a markdown body."""
    normalized = (body or "").replace("\r\n", "\n")
    pattern = rf"(?:^|\n)## {re.escape(heading)}\s*\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, normalized, re.DOTALL)
    if m:
        return m.group(1).strip()
    if normalized:
        logger.debug(
            "_extract_section: section '%s' not found in body (len=%d)", heading, len(normalized)
        )
    return ""


# ---------------------------------------------------------------------------
# Jinja2 setup
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
)

_COOKIE_NAME = "lachesis_web_session"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days, matches JWT expiry
_OAUTH_STATE_COOKIE = "lachesis_oauth_state"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render(template_name: str, **ctx: Any) -> HTMLResponse:
    tmpl = _jinja_env.get_template(template_name)
    return HTMLResponse(tmpl.render(**ctx))


def _get_web_user_status(
    request: Request,
) -> tuple[str, dict[str, Any] | None]:
    """Decode the web session cookie.

    Returns:
        ("ok", payload)      — valid session
        ("expired", None)    — JWT exists but has expired
        ("missing", None)    — no cookie, invalid token, or wrong email
    """
    token = request.cookies.get(_COOKIE_NAME)
    if not token or not settings.secret_key:
        return ("missing", None)
    try:
        payload = jose_jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except ExpiredSignatureError:
        return ("expired", None)
    except JWTError as exc:
        logger.debug("_get_web_user_status: JWT decode failed: %s", exc)
        return ("missing", None)
    email = payload.get("email", "")
    if settings.allowed_email and email != settings.allowed_email:
        return ("missing", None)
    return ("ok", cast(dict[str, Any], payload))


def _google_auth_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "prompt": "select_account",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def web_index(request: Request) -> Response:
    """GET /web/ -- redirect to login if no valid session, else show dashboard."""
    status, user = _get_web_user_status(request)
    if status == "expired":
        resp = RedirectResponse(url="/web/login?reason=expired", status_code=302)
        resp.delete_cookie(_COOKIE_NAME)
        return resp
    if status != "ok":
        return RedirectResponse(url="/web/login", status_code=302)

    assert user is not None  # guaranteed when status == "ok"
    lachesis_uid = user.get("lachesis_uid")
    if not lachesis_uid:
        logger.warning("web_index: JWT has no lachesis_uid for sub=%s", user.get("sub"))
        resp = RedirectResponse(url="/web/login", status_code=302)
        resp.delete_cookie(_COOKIE_NAME)
        return resp

    # Fetch repos
    repos_raw = db.list_repos(lachesis_uid)

    # Build dashboard data per repo
    repos_data: list[dict[str, Any]] = []
    for repo_row in repos_raw:
        repo_name = repo_row["repo_name"]
        requirements: list[dict[str, Any]] = []
        try:
            files = await github.list_files(repo_name, "requirements")
            # Collect all issue numbers to fetch concurrently
            parsed: list[dict[str, Any]] = []
            for f in files:
                if not f["name"].endswith(".md"):
                    continue
                try:
                    content, _ = await github.get_file(repo_name, f["path"])
                    post = frontmatter.loads(content)
                except Exception as exc:
                    logger.warning(
                        "web_index: failed to parse file repo=%s path=%s: %s",
                        repo_name,
                        f["path"],
                        exc,
                    )
                    continue
                parsed.append(
                    {
                        "id": post.get("id", ""),
                        "title": post.get("title", ""),
                        "status": canonical_status(post.get("status", "")),
                        "github_issue": post.get("github_issue"),
                        "why": _extract_section(post.content, "Why"),
                        "what": _extract_section(post.content, "What"),
                    }
                )

            # Batch-fetch issue states concurrently
            issue_numbers = [r["github_issue"] for r in parsed if r["github_issue"]]
            issue_tasks = [github.get_issue(repo_name, n) for n in issue_numbers]
            issue_results = await asyncio.gather(*issue_tasks, return_exceptions=True)
            issue_map: dict[int, dict[str, Any]] = {}
            for num, result in zip(issue_numbers, issue_results, strict=False):
                if isinstance(result, dict):
                    issue_map[num] = result
                else:
                    logger.warning(
                        "web_index: get_issue failed repo=%s issue=%s: %s", repo_name, num, result
                    )

            for req in parsed:
                inum = req["github_issue"]
                requirements.append(
                    {
                        **req,
                        "issue": issue_map.get(inum) if inum else None,
                    }
                )
        except Exception as exc:
            logger.warning("web_index: failed to load repo=%s: %s", repo_name, exc)

        spec_changes: list[dict[str, Any]] = []
        spec_gaps: dict[str, int] = {}
        try:
            if await uses_specs(repo_name):
                progress = await read_spec_progress(repo_name)
                spec_gaps = progress.spec_gaps
                spec_changes = [
                    {
                        "number": change["spec_change"],
                        "title": change["title"],
                        "url": change["url"],
                        "status": "dropped" if change["dropped"] else spec_change_status(change),
                        "issue_count": len(change["derived"]["linked_issues"]),
                    }
                    for change in progress.spec_changes
                ]
        except Exception as exc:
            logger.warning("web_index: failed to load spec changes repo=%s: %s", repo_name, exc)

        repos_data.append(
            {
                "name": repo_name,
                "requirements": requirements,
                "spec_changes": spec_changes,
                "spec_gaps": spec_gaps,
            }
        )

    return _render(
        "dashboard.html",
        user_email=user.get("email"),
        repos=repos_data,
    )


async def web_login(request: Request) -> Response:
    """GET /web/login -- show login page with Google sign-in button."""
    status, user = _get_web_user_status(request)
    if status == "ok":
        return RedirectResponse(url="/web/", status_code=302)

    expired = request.query_params.get("reason") == "expired"
    state = secrets.token_urlsafe(16)
    base = settings.mcp_base_url.rstrip("/")
    redirect_uri = f"{base}/web/callback"
    login_url = _google_auth_url(redirect_uri, state)

    response = _render("login.html", user_email=None, login_url=login_url, expired=expired)
    # Store state in short-lived cookie (10 min) to verify in callback
    response.set_cookie(
        _OAUTH_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.mcp_base_url.startswith("https"),
    )
    return response


async def web_callback(request: Request) -> Response:
    """GET /web/callback -- Google OAuth callback; sets session cookie."""
    qp = request.query_params
    code = qp.get("code", "")
    state = qp.get("state", "")
    error = qp.get("error", "")

    if error:
        logger.warning("web_callback: Google returned error=%s", error)
        return RedirectResponse(url="/web/login", status_code=302)

    stored_state = request.cookies.get(_OAUTH_STATE_COOKIE, "")
    if not code or not state or state != stored_state:
        logger.warning("web_callback: state mismatch or missing code")
        return RedirectResponse(url="/web/login", status_code=302)

    base = settings.mcp_base_url.rstrip("/")
    redirect_uri = f"{base}/web/callback"
    user_data = await exchange_google_code(code, redirect_uri)
    if user_data is None:
        logger.warning("web_callback: Google exchange failed")
        return RedirectResponse(url="/web/login", status_code=302)

    google_id = user_data["id"]
    email = user_data["email"]

    lachesis_uid: str | None = None
    try:
        db_user = db.upsert_user(google_id, email)
        lachesis_uid = str(db_user["id"])
    except Exception as exc:
        logger.warning("web_callback: upsert_user failed for google_id=%s: %s", google_id, exc)

    token = create_jwt(google_id, email, lachesis_uid)

    response = RedirectResponse(url="/web/", status_code=302)
    response.set_cookie(
        _COOKIE_NAME,
        token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.mcp_base_url.startswith("https"),
    )
    # Clear the state cookie
    response.delete_cookie(_OAUTH_STATE_COOKIE)
    logger.info(
        "web_callback: session established for email=%s lachesis_uid=%s", email, lachesis_uid
    )
    return response


async def web_logout(request: Request) -> Response:
    """GET /web/logout -- clear session cookie."""
    response = RedirectResponse(url="/web/login", status_code=302)
    response.delete_cookie(_COOKIE_NAME)
    return response
