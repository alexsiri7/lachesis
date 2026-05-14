---
created: '2026-05-14'
github_issue: null
id: '011'
status: idea
title: Project dashboard with Google OAuth
updated: '2026-05-14'
---

## Why

Currently there is no way to discover which repos lachesis is tracking without already knowing the repo name. The web UI is unprotected and has no useful content. A dashboard gives a single place to see the health of all projects at a glance, both via browser and via MCP.

## What

A web dashboard at lachesis.interstellarai.net protected by Google OAuth (single-user, Alex only). The dashboard shows all tracked repos with a summary per repo: total requirements and breakdown by status (todo / in-progress / done). Also expose a new `get_dashboard` MCP tool that returns the same cross-repo summary for use in Claude.

## Issues

_None yet._