---
created: '2026-05-26'
github_issue: 82
id: '020'
status: superseded
superseded_by:
  repo: alexsiri7/lachesis
  spec_change: 156
title: Issue dependency tracking via GitHub Projects v2
updated: '2026-09-26'
---

## Why

There's currently no way to express that one task must land before another — the `blocked_by` field in `create_github_issues` exists but does nothing. Without real dependency tracking, Archon has no way to know the correct build order (e.g. the task list UI depends on the task data model, the UI for beat insertion depends on the approval gate being removed). GitHub Projects v2 is the right layer for this: it's queryable, visual, and keeps the data in GitHub rather than a separate system.

## What

Lachesis gains two capabilities: `add_dependency(repo, issueNumber, blockedByIssueNumber)` to declare that one issue depends on another, and `get_dependencies(repo, issueNumber)` to query what an issue is blocked by or blocking. Dependencies are tracked via GitHub Projects v2's native dependency field rather than freeform body text, making them queryable and visible on the project board. The existing `blocked_by` field in `create_github_issues` is wired up to this rather than being silently ignored as it is now.

## Issues

- #82 — Wire up blocked_by in create_github_issues to GitHub Projects v2 dependencies
- #83 — Implement add_dependency MCP tool
- #84 — Implement get_dependencies MCP tool