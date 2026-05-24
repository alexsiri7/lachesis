---
created: '2026-05-24'
github_issue: 72
id: '015'
status: idea
title: Handle main branch protection when writing requirements
updated: '2026-05-24'
---

## Why

Repos with branch protection on main cause Lachesis to fail with a 409 when trying to write requirement files directly via the GitHub Contents API. This silently blocks requirement creation with a confusing error rather than adapting to the repo's workflow.

## What

When Lachesis cannot write directly to main due to branch protection rules, it automatically creates a short-lived branch, commits the requirement file there, and opens a pull request. The PR title and description make it clear it is a Lachesis-generated requirement. The tool call still returns success with the requirement details, and includes the PR URL so the author can review and merge.

## Issues

- #72 — Detect 409 on main and fall back to branch + PR flow
- #73 — Include PR URL in create_requirement response when branch flow is used