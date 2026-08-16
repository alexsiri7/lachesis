# Lachesis

Hosted MCP server for lightweight requirements management, scoped per GitHub repository.

Lachesis captures requirements as Markdown files inside each repo (`requirements/*.md`), links them to GitHub Issues, and exposes them to Claude via MCP tools.

## Stack

- **Language**: Python (FastMCP)
- **Hosting**: Railway — `lachesis.interstellarai.net`
- **Auth**: MCP OAuth 2.1 (Google OAuth, single-user)
- **Storage**: Markdown files written directly to GitHub repos via GitHub API
- **Persistence**: Supabase (user identity and repo registry; `lachesis_` namespaced tables)

## Docs

- [Product Requirements Document](docs/PRD.md)

## MCP Tools

`list_requirements` · `get_requirement` · `create_requirement` · `update_requirement` · `delete_requirement` · `link_issues` · `create_github_issues` · `get_github_issue` · `update_github_issue` · `get_dependencies` · `add_dependency`

## Integrating with Lachesis

> **Note**: The `get_initial_instructions` tool is planned but not yet implemented (see #74).

Once available, integrations (Claude Code, Archon, or any MCP client) should call `get_initial_instructions` at the start of every session. This will return the workflow guidelines Claude needs to work correctly with Lachesis — for example, that every `create_requirement` call should be followed by `create_github_issues` and `link_issues` so the requirement reaches `idea` status with actionable tasks attached.

The guidelines will live on the Lachesis server, so they stay in sync automatically. No external system prompt maintenance required.

## Development

```bash
cd mcp
pip install -r requirements.txt
uvicorn main:app --reload
```
