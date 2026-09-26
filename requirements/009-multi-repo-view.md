---
created: '2026-05-12'
github_issue: 27
id: 009
status: superseded
superseded_by:
  repo: alexsiri7/lachesis
  spec_change: 156
title: Multi-repo requirements view
updated: '2026-09-26'
---

## Why

The portfolio spans 8+ repos. A single view aggregating requirements across all projects (filterable by status) would help prioritize work across the whole portfolio.

## What

Aggregate requirements across all repos in the user's registry, with cross-repo filtering by status (`in_progress`, `planned`, `idea`). Also expose a `get_dashboard` MCP tool returning the same cross-repo summary for use in Claude. Depends on repo registry (012).
## Issues

## Issues

- #27 — Multi-repo requirements view + get_dashboard MCP tool