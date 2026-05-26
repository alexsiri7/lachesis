---
created: '2026-05-26'
github_issue: null
id: '022'
status: draft
title: Lachesis web UI — OAuth check and dashboard view
updated: '2026-05-26'
---

## Why

The web UI currently shows nothing. It is the primary non-Claude way to get an overview of all requirements across repos, but it is unusable. The MCP dashboard call already has the right data shape — the UI just needs to surface it with auth in place.

## What

The Lachesis web UI checks OAuth state on load. If the user is not authenticated, it prompts them to connect. Once authenticated, it displays the same cross-repo requirements summary as `get_dashboard` — all requirements grouped by repo and status, with title, status, and linked issue count visible at a glance. The view updates when requirements change.

## Issues

_None yet._