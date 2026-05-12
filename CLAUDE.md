# Lachesis — Developer Guide

## What this is

A hosted FastMCP server providing 7 MCP tools for requirements management, writing/reading Markdown files directly to GitHub repos via the GitHub API.

See [docs/PRD.md](docs/PRD.md) for the full product spec.

## Reference implementation

`alexsiri7/kindred` is the canonical reference for this project's MCP/OAuth patterns:
- FastMCP with Streamable HTTP transport
- MCP OAuth 2.1 (`oauth.py`, `oauth_state.py`) — adapt to Google OAuth (Kindred uses Supabase; replace the Supabase token exchange with Google's)
- `settings.py` using pydantic-settings `BaseSettings` (Kindred uses a `CoreSettings` base; simplified here since Lachesis has a single settings module)
- Railway via `mcp/Dockerfile` + `railway.toml`
- Sentry integration via `sentry_sdk`

## Key constraints

- **Single-user** — auth must allowlist exactly one Google account, email loaded from `ALLOWED_EMAIL` env var
- **No personal data in this repo** — the `ALLOWED_EMAIL` and `GITHUB_PAT` live in env vars / Railway secrets only
- **GitHub API only** — all file reads/writes go through the GitHub REST API using a server-side PAT (`GITHUB_PAT` env var). No local disk I/O for requirement files.
- **`delete_requirement` must refuse `done` status** — see PRD for rationale

## Project layout

```
mcp/               FastMCP app (mirrors kindred/mcp/)
  main.py          FastMCP instance, tool registration, OAuth routes, ASGI bearer-token middleware
  auth.py          JWT decode helper (resolve_user_id_from_jwt), current_user_id ContextVar
  oauth.py         OAuth 2.1 endpoints (Google variant)
  oauth_state.py   In-memory state stores (auth codes, sessions, refresh tokens)
  settings.py      Pydantic-settings config
  github.py        GitHub API client (PAT-authenticated)
  tools/
    __init__.py
    requirements.py  All 7 MCP tool implementations
  Dockerfile
  pyproject.toml
  requirements.txt
docs/
  PRD.md
railway.toml
.env.example
```

## Environment variables

See `.env.example`. Key ones:
- `GITHUB_PAT` — server-side GitHub token with `repo` scope
- `ALLOWED_EMAIL` — the single authorized Google account email
- `SECRET_KEY` — JWT signing key
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — Google OAuth app credentials
- `MCP_BASE_URL` — public URL, e.g. `https://lachesis.interstellarai.net`
- `MCP_ALLOWED_HOSTS` — comma-separated hostnames for DNS rebinding protection (set to the Railway domain in production)
- `SENTRY_DSN` — optional; enables Sentry error tracking

## Running locally

```bash
cd mcp
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
