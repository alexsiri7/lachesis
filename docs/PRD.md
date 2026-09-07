# Lachesis — Product Requirements Document

## The Name

In Greek mythology, the **Moirai** were the three Fates who controlled the destiny of every living thing. Of the three, **Lachesis** was the Measurer — she who drew the thread of life from Clotho's spindle and decided how much each being would receive. She allocated. She planned. She determined what would exist, for how long, and in what form.

A software requirement is a thread: an intention that must be measured, shaped, and woven into the fabric of a working system before it can be real. Lachesis is the tool that holds those threads — capturing them before they scatter, giving them form, and tracking them until they are woven in.

---

## Overview

Lachesis is a hosted MCP server that provides lightweight requirements management for software projects, scoped per GitHub repository. It acts as the structured layer between a developer's ideas and their GitHub issues — capturing intent, preserving the *why*, and linking requirements to the GitHub issues that implement them.

It is designed to be used conversationally through Claude, with Claude providing the reasoning and validation layer, and Lachesis providing the storage and GitHub integration.

---

## Problem

Solo developers working across multiple projects lose requirements at every stage of the pipeline:

- Ideas arrive randomly and get forgotten before they are captured
- When captured (in notes, chat, or a PRD), the *why* gets lost in translation
- PRDs become stale snapshots rather than living documents
- Work gets built from memory rather than from intent
- There is no closure: requirements are never formally marked done

---

## Design Principles

- **Frictionless capture** — requirements are created conversationally via Claude, not through a form or UI
- **Markdown-native** — requirements live as `.md` files inside each repo, versioned alongside the code
- **GitHub is the state machine** — Lachesis does not duplicate issue status; GitHub Issues are the source of truth for progress
- **Claude does the thinking** — validation, conflict detection, and issue planning are Claude's job; Lachesis provides context and acts on instructions
- **Requirements live in the repo** — requirement files are Markdown, stored in the GitHub repo alongside code; there is no separate query interface for requirements data

---

## Architecture

### Stack
- **Language**: Python (FastMCP)
- **Hosting**: Railway, deployed under `lachesis.interstellarai.net`
- **Auth**: Google OAuth (single-user, allowlisted by email via env var); Claude.ai connects via MCP OAuth 2.1
- **Storage**: Markdown files written directly to GitHub repos via GitHub API (no local filesystem dependency); Supabase stores user identity and repo registry (`lachesis_` namespaced tables)

### Repository Structure

Each connected repo gets a `requirements/` folder at its root:

```
requirements/
  001-user-auth.md
  002-elo-ranking.md
  003-trakt-integration.md
```

### Requirement File Format

```markdown
---
id: "001"
title: "User authentication"
status: "planned"
github_issue: 42
created: "2026-05-11"
updated: "2026-05-11"
---

## Why

Users need to save their film rankings across sessions and devices.

## What

Implement email/password authentication with JWT sessions. Support Google OAuth as a secondary option.

## Issues

- #42 — [Auth] Set up FastAPI JWT middleware
- #43 — [Auth] Build login and registration endpoints
- #44 — [Frontend] Login and registration screens
```

### Requirement vs Issue

A **requirement** captures *intent* and *desired behaviour*:
- `why` — the motivation or problem being solved
- `what` — the user-visible outcome, described in behavioural terms

A **GitHub issue** captures *implementation work* — the concrete steps needed to build the behaviour described in the requirement.

**Requirements must never describe implementation steps.** Writing "Implement JWT middleware" in `what` is wrong; "Users can authenticate with their Google account" is correct.

Every requirement should be paired with at least one GitHub issue to be actionable. A requirement with no linked issues is a note, not a plan.

**Status values**: `draft` → `idea` → `planned` → `in-progress` → `done`

- **draft** — captured but not yet validated or shaped; may be incomplete or exploratory
- **idea** — validated intent, not yet planned for implementation
- **planned** — scoped with linked GitHub issues, ready to build
- **in-progress** — active implementation underway
- **done** — implemented; `delete_requirement` refuses to delete requirements in this state

Status is set manually or by Claude based on GitHub issue state — Lachesis does not auto-sync.

### ID and filename assignment

- IDs are assigned as zero-padded 3-digit integers: `001`, `002`, etc.
- Filename slug is derived from the title: lowercase, spaces → hyphens, non-alphanumeric stripped.
- ID is determined by listing existing files and incrementing the highest existing ID.
- In the event of a slug collision (same title words), append `-2`, `-3`, etc.
- `create_requirement` creates the `requirements/` folder via GitHub API if it does not exist.

### delete_requirement behaviour

`delete_requirement` **must refuse** to delete a requirement with status `done`. A done requirement represents implemented code — removing the file does not remove the code, which would leave the repo in an inconsistent state. To remove an implemented feature, create a new requirement describing its removal.

---

## MCP Tools

### `get_initial_instructions`
Returns the standard Lachesis workflow. Call this at the start of a new session to orient Claude before capturing requirements.

**Input**: _(none)_
**Output**: String — the standard workflow steps (create_requirement → create_github_issues → link_issues)

---

### `list_requirements`
Returns all requirements for a given repo.

**Input**: `repo` (string, e.g. `alexuser/filmduel`)
**Output**: Array of requirement summaries (id, title, status, github_issue).
For requirements on open PR branches, each item also includes: branch, pr_number, pr_url.
Each item also carries a `derived` block read from its linked issues and their PRs:
`status` (idea, in-progress, done, or null), `source` (`issues` when the status was derived,
`unlinked` when the requirement links no issues, `unavailable` when GitHub could not be read —
a null `status` means "no work started" only under `unlinked`), `diverged` (the recorded status
disagrees), `last_activity`, `linked_issues`, and `signals`. Cancelled and superseded are never
derived.

---

### `get_requirement`
Returns the full content of a single requirement file.

**Input**: `repo`, `id`
**Output**: Full markdown content

---

### `create_requirement`
Creates a new requirement markdown file in the repo.

**Input**: `repo`, `title`, `why`, `what`
**Output**: Created file path and auto-assigned id

---

### `update_requirement`
Updates fields in an existing requirement file (title, why, what, status).

**Input**: `repo`, `id`, `fields` (partial update)
**Output**: Updated file content

---

### `delete_requirement`
Removes a requirement file from the repo. Refuses if status is `done`.

**Input**: `repo`, `id`
**Output**: Confirmation, or error if status is `done`

---

### `link_issues`
Writes a list of GitHub issue numbers and titles into the requirement's Issues section.
Sets the first issue as `github_issue` in the frontmatter (the top-level tracker).

**Input**: `repo`, `id`, `issues` (array of `{number, title}`)
**Output**: Updated requirement file

---

### `create_github_issues`
Creates one or more GitHub issues in the repo and returns their numbers and titles.

**Input**: `repo`, `issues` (array of `{title, body, labels?}`)
**Output**: Array of created `{number, title, url}`

---

### `get_github_issue`
Returns the current state of a GitHub issue.

**Input**: `repo`, `issue_number` (integer)
**Output**: `{id, number, title, body, state, assignees, labels, linked_pr, url}`

---

### `update_github_issue`
Updates a GitHub issue's title, body, and/or state.

**Input**: `repo`, `issue_number` (integer), `fields` (object — any combination of `title`, `body`, `state` (`"open"` or `"closed"`))
**Output**: `{number, title, url}` of the updated issue

---

### `get_dependencies`
Returns the dependency relationships of a GitHub issue.

**Input**: `repo`, `issue_number` (integer)
**Output**: `{blocked_by: [int, ...], blocking: [int, ...]}` — lists of issue numbers this issue depends on and issues that depend on it

---

### `add_dependency`
Marks a GitHub issue as blocked by another issue.

**Input**: `repo`, `issue_number` (integer), `blocked_by_issue_number` (integer)
**Output**: `{blocked_by: [int, ...], blocking: [int, ...]}` — updated dependency state after the relationship is created

---

## Typical Workflow

### Capturing a new requirement

1. Developer describes an idea to Claude mid-conversation
2. Claude calls `list_requirements` to load existing requirements for that repo
3. Claude checks for conflicts or overlap, asks any clarifying questions
4. Claude calls `create_requirement` with a structured title, why, and what
5. Claude suggests a breakdown of GitHub issues
6. Developer approves; Claude calls `create_github_issues` then `link_issues`

### Closing a requirement

1. Developer tells Claude the work is done
2. Claude calls `update_requirement` to set status to `done`
3. The GitHub issues are already closed on GitHub — no sync needed

### Starting a session

1. Developer starts a new Claude session with Lachesis connected
2. Claude calls `get_initial_instructions` to load the prescribed workflow
3. Developer asks what's in progress across a repo
4. Claude calls `list_requirements` filtered by `in_progress`
5. Claude summarises open requirements and their linked issues

---

## Future Considerations

- **Multi-repo view** — aggregate requirements across all projects
- **Requirement templates** — per-project templates for the `why` / `what` structure
- **Changelog generation** — generate a release changelog from `done` requirements

---

## Out of Scope (v1)

- Automatic status sync from GitHub issue state
- Notifications or reminders
- Team/multi-user support
- Priority or ordering beyond file naming convention
