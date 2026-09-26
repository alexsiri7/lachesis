---
created: '2026-05-26'
github_issue: 86
id: '022'
status: superseded
superseded_by:
  repo: alexsiri7/lachesis
  spec_change: 156
title: Lachesis web UI — OAuth check and dashboard view
updated: '2026-09-26'
---

## Why

The web UI currently shows nothing. It is the primary non-Claude way to get an overview of all requirements across repos, but it is unusable. The MCP dashboard call already has the right data shape — the UI just needs to surface it with auth in place.

## What

The Lachesis web UI checks OAuth state on load. If the user is not authenticated, it prompts them to connect. Once authenticated, it displays the same cross-repo requirements summary as `get_dashboard` — all requirements grouped by repo and status, with title, status, and linked issue count visible at a glance. The view updates when requirements change.

## Issues

- #86 — Web UI: OAuth check and connect flow on load
- #87 — Web UI: render dashboard view matching get_dashboard output