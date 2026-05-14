---
created: '2026-05-14'
github_issue: null
id: '012'
status: idea
title: Repo registry
updated: '2026-05-14'
---

## Why

Currently lachesis has no concept of which repos it manages — the caller must always supply a repo name. A registry enables cross-repo features like the dashboard and multi-repo view, and is the foundation for any portfolio-level tooling.

## What

A mechanism for users to register which repos lachesis tracks, stored in Supabase (`lachesis_repos` table, scoped by user). MCP tools: `register_repo`, `unregister_repo`, `list_repos`. Depends on the Supabase persistence layer (011).

## Issues

_None yet._