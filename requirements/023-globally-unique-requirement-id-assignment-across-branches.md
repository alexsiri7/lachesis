---
created: '2026-06-03'
github_issue: 89
id: '023'
status: idea
title: Globally unique requirement ID assignment across branches
updated: '2026-06-03'
---

## Why

ID assignment currently only looks at main. When multiple requirements are created in quick succession before any are merged, every new requirement gets the same "next" ID (all read the same last ID from main), producing files like 015-foo.md, 015-bar.md, 015-baz.md. This breaks linking, causes merge conflicts, and makes the ID system unreliable.

## What

When assigning an ID to a new requirement, Lachesis checks for existing requirement files across all open PR branches as well as main. The assigned ID is the next one not already claimed anywhere in the repo. No two requirement files, whether on main or in any open branch, share the same numeric prefix.

## Issues

- #89 — Scan open PR branches for existing requirement IDs before assigning next ID