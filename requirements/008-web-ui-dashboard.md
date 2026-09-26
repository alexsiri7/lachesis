---
created: '2026-05-12'
github_issue: 26
id: 008
status: superseded
superseded_by:
  repo: alexsiri7/lachesis
  spec_change: 156
title: Web UI (read-only dashboard)
updated: '2026-09-26'
---

## Why

A simple visual dashboard showing requirements per repo with their linked issue status would give an at-a-glance view of project health without opening a chat.

## What

Read-only web app at lachesis.interstellarai.net protected by Google OAuth (single user for now, multi-user ready). Lists requirements per repo from the registry, grouped by status, with links to linked GitHub issues and their open/closed state. Depends on repo registry (012).
## Issues

## Issues

- #26 — Web UI (read-only dashboard) with Google OAuth