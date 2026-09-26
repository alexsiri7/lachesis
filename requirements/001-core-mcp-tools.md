---
id: "001"
title: "Core MCP tools (CRUD for requirements)"
status: "superseded"
superseded_by:
  repo: "alexsiri7/lachesis"
  spec_change: 156
github_issue: null
created: "2026-05-12"
updated: "2026-09-26"
---

## Why

The primary value of Lachesis is giving Claude structured read/write access to requirement files stored in GitHub repos. Without these tools, the system doesn't exist.

## What

Seven MCP tools: `list_requirements` (all requirements for a repo), `get_requirement` (full content of one file), `create_requirement` (new file with auto-assigned ID and slug), `update_requirement` (partial update of fields), `delete_requirement` (refuses if status is `done`), `link_issues` (write issue numbers into the requirement's Issues section), `create_github_issues` (create one or more GitHub issues and return their numbers/URLs).

## Issues

- #4 — [Tool] list_requirements + get_requirement
- #5 — [Tool] create_requirement
- #6 — [Tool] update_requirement
- #7 — [Tool] delete_requirement
- #9 — [Tool] create_github_issues
