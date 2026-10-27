# Lachesis — Developer Guide

## What this is

A hosted FastMCP server providing 12 MCP tools for requirements management, writing/reading Markdown files directly to GitHub repos via the GitHub API.

See [docs/PRD.md](docs/PRD.md) for the full product spec.

## Reference implementation

`alexsiri7/kindred` is the canonical reference for this project's MCP/OAuth patterns:
- FastMCP with Streamable HTTP transport
- MCP OAuth 2.1 (`oauth.py`, `oauth_state.py`) — adapt to Google OAuth (Kindred uses Supabase; replace the Supabase token exchange with Google's)
- `settings.py` extending a `CoreSettings` base (pydantic-settings)
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
  main.py          FastMCP instance, tool registration, OAuth routes
  auth.py          Bearer token middleware, user resolution
  oauth.py         OAuth 2.1 endpoints (Google variant)
  oauth_state.py   In-memory state stores (auth codes, sessions, refresh tokens)
  settings.py      Pydantic-settings config
  github.py        GitHub API client (PAT-authenticated)
  db.py            SQLAlchemy persistence layer (user identity, repo registry)
  web.py           Web dashboard route handlers and Jinja2 setup
  notify.py        Best-effort ntfy delivery (never raises into a caller)
  landing.py       Detects landed requirements and spec changes on read, and fires their notification
  spec_reference.py Writes and parses the spec reference block at the end of an issue body
  unattended_merges.py Configures a repo so its pull requests merge without a human (auto-merge, branch deletion, no required approving review), returning what GitHub refused; register_repo, create_project and each start run it and record the refusals for get_dashboard's lachesis_faults
  tools/
    __init__.py
    notifications.py ntfy configuration and test-notification tools
    events.py        list_events: follow the append-only event log from a cursor
    fuel.py          report_usage: every run on an issue records its model, tokens in and out, cost where paid per token, and kind of work; register_account and list_accounts keep the Claude accounts the factory may use (share of each weekly allowance, five-hour pause threshold, one primary); report_fuel stores each account's latest allowance status; report_overflow stores the Requesty spend and cap on their own (nothing routes to overflow until it is reported); read_fuel returns every account with its reserve (10% of its share, released for any work in the last 24 hours before its reset), usable allowance, pause and pace, and the primary's figures at the top level
    audits.py        report_audit_run records every audit run; read_audit_history derives each repo's last run per kind, its findings still open, whether it is due (never run, failed, merges since, new auditor model, security every 60 days) and its open audit issue, ordered by merges, each estimated from past runs of its kind (low confidence below five), and when each capability was last audited, which get_dashboard and the web view show too; next_audit offers next_issue the first due audit that may start (none in a repo whose audit issue is open; within the budget and pace like an issue); start_audit opens the audit issue (one open at a time per repo, least recently audited capability for tests/requirements in spec repos; a north-star audit, due only where docs/north-star.md exists, lists its stories); file_audit_finding files spec-gap findings, decisions waiting on the author
    estimates.py     size_issue: an issue's size in Fibonacci points (21 = larger than 13, planned as splitting it); estimate_issue: an issue's token estimate from the measured usage of past closed issues of the same kind and capability, low confidence below five, stored per issue
    scorecard.py     report_run_outcome records rework, a retry or an audit finding against the latest run of a kind on an issue; read_scorecard derives per model (each Claude model on its own), kind and issue size runs, merged without rework, retries, audit findings, tokens, cost, tokens and cost per point, trust at a size (5 consecutive clean runs at that size or larger; covers smaller sizes; lost at every size at 2 failures in the last 5 runs at the sizes it covers, and not earned while they stand, whether they came before or after the streak; an unsized run counts at 1 point) and may_trial (triage and summarising first, then kinds above a trusted one) on every read, never stored
    routing.py       route_run: the model for a run of a kind and size, and for Claude the account that pays for it (most usable allowance, not paused, within pace unless an emergency; the primary on a tie) — among models trusted for the kind at the size, or on trial one size above their trust (a trial winning a tie), the cheapest per point: an overflow model when it beats every Claude model while overflow is under the Requesty cap, else the cheapest Claude model on the allowance, else the cheapest overflow model once the allowance outside the reserve is spent or a shorter limit is in force; claude trusted for every kind and size by default; no trial once the sprint's trials have spent the trial share, nor to redo a trial that failed on the issue named; derived on every read, never stored
    milestones.py    Milestones grouping spec changes: create, start, drop, add and remove spec changes, get and list, and review_milestone returning a started milestone's gaps; state (planned, active, verifying, done, dropped) is derived on every read from the spec changes' issues and the production deployment, never stored
    sprints.py       read_sprint: the sprint from one reset of the primary account's weekly allowance to the next, its budget in tokens (every account's share less its reserve, unless released in its last 24 hours before the reset, plus the overflow cap), spending, and the plan — the due audits and the computed backlog as far as estimates fit what is left; derived on every read, never stored; next_issue holds work other than security outside the budget or pace; report_sprint reports the sprint that last ended once, listing the audits that ran with their findings, storing the report, noting each miss as a finding on its issue and sending one push notification, and lists every open experiment; trials (issues route_run would give a model on trial) are planned first, within the trial share of the budget (TRIAL_SHARE_PERCENT, 10 by default), then the open experiments' issues, within the improvement share (IMPROVEMENT_SHARE_PERCENT, 10 by default)
    experiments.py   record_experiment: an improvement to how the factory works, with the measure it should move, its baseline and target, and the issues holding its work; judge_experiment at a sprint review: kept, reverted or extended one more sprint, at most once
    focus.py         set_focus and clear_focus: the author's focus on repositories or milestones, which puts their issues in the focus tier of the computed backlog order
    notes.py         add_note and list_notes: append-only work notes on exactly one issue or milestone; next_issue returns the offered issue's notes
    questions.py     ask_question: record a question for the author and mark the issues it blocks needs-author, pushing it to the author; ask_author asks one about a single issue through it; answer_question records an answer and its outcome, posts it on the blocked issues and releases those no other open question blocks; answer_author answers a single issue through it; list_open_questions is the one queue of open questions across registered repos, oldest first; search_answered_questions finds answered questions by repo and text, with answer and outcome; a clarification or improvement question may carry a default, which settle_expired_defaults takes (outcome defaulted) once its wait passes, on every read of the open questions (list_open_questions, next_issue, the dashboards), where settle_closed_questions also answers each question whose blocked issues are all closed; list_defaulted_questions lists those settled by default in the last hours, for the morning briefing
    contents.py      Read-only repository contents: read_file, list_directory (registered repos only)
    projects.py      create_project: create a GitHub repository, seed its openspec/specs and register it, rolling back on failure
    specs.py         Spec tools: read (list_capabilities, get_capability), change (propose_spec_change, author_request labelling the author's requests author-request, update_spec_change, with format checking), and the uses_specs detection helper
    requirements.py  All 12 MCP tool implementations (includes read-only get_initial_instructions, get_morning_briefing, get_github_issue, get_comments, get_dependencies; write tools add_dependency, post_comment, answer_author, update_github_issue)
  templates/
    dashboard.html   Read-only requirements dashboard (Jinja2)
  Dockerfile
  pyproject.toml
  requirements.txt
tests/acceptance/   One acceptance test per spec scenario (see "Acceptance tests" below)
  harness/         The only route into the app: MCP over HTTP, GitHub and ntfy stubbed, a disposable Postgres
  coverage.yml     `enforce:` turns scenario-coverage gaps from a report into a failure
plugins/plan-usage/ Claude Code plugin for budgeted accounts: usage tool, status line, budget guard, plan-usage.json for fuel reporting (this repo is its marketplace: .claude-plugin/marketplace.json)
ci/                Delivery checks run by .github/workflows/delivery.yml and ci.yml, with their unit tests
migrations/
  001_lachesis_tables.sql   DDL for lachesis_users and lachesis_repos
docs/
  PRD.md
railway.toml
.env.example
```

## Environment variables

See `.env.example`. Key ones:
- `GITHUB_PAT` — server-side GitHub token with `repo` scope, plus `delete_repo` so a failed `create_project` can delete the repository it created; a fine-grained token also needs Checks (read), without which a private repository's scenario results cannot be read
- `ALLOWED_EMAIL` — the single authorized Google account email
- `SECRET_KEY` — JWT signing key
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — Google OAuth app credentials (**both required**; register `{MCP_BASE_URL}/oauth/callback` as an authorized redirect URI in the Google OAuth Console)
- `MCP_BASE_URL` — public URL, e.g. `https://lachesis.interstellarai.net`
- `DATABASE_URL` — Postgres connection string (e.g. `postgresql://postgres:[password]@db.<ref>.supabase.co:5432/postgres`); store in Railway secrets only

## Supabase setup

Run the Alembic migration once against the Supabase instance before deploying.
Configure `DATABASE_URL` in your environment, then from the `mcp/` directory:

```bash
cd mcp
alembic upgrade head
```

Tables are namespaced `lachesis_` to coexist with other projects on the same instance.

## Running locally

```bash
cd mcp
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Acceptance tests

Every `#### Scenario:` in `openspec/specs/` gets exactly one test under
`tests/acceptance/<capability>/`, declared with `@scenario("<capability>", "<title>")` from
`harness`. A test the code does not yet satisfy carries
`@pytest.mark.xfail(strict=True, reason="spec change #N")`; once it passes, the marker must go.
Tests use only `harness` (the `lachesis` fixture calls MCP tools; `lachesis.github` stubs
GitHub; `lachesis.ntfy` records push notifications; `lachesis.restart()` restarts the app) and
never import from `mcp/`.
Implementation pull requests may only remove xfail markers from these tests; spec changes and
`acceptance-tests` issues may change them.

The harness migrates the Postgres database named by `ACCEPTANCE_DATABASE_URL`, connecting as
a superuser, and empties every table but the user's before each test: use a throwaway database.

```bash
ACCEPTANCE_DATABASE_URL=postgresql://postgres@localhost:5432/lachesis_acceptance \
  pytest tests/acceptance   # acceptance tests
pytest ci                   # delivery check unit tests
```
