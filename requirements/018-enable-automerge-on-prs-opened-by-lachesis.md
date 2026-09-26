---
created: '2026-05-24'
github_issue: 79
id: 018
status: superseded
superseded_by:
  repo: alexsiri7/lachesis
  spec_change: 156
title: Enable automerge on PRs opened by Lachesis
updated: '2026-09-26'
---

## Why

Lachesis PRs are mechanical — they contain a single generated file with no logic to review. Requiring a human to manually merge them adds friction with no benefit. Automerge means the workflow stays uninterrupted: create requirement, PR opens, merges itself, requirement is on main.

## What

When Lachesis opens a PR via the branch protection fallback, it immediately enables automerge on that PR (squash or merge commit, whichever the repo prefers). The requirement file lands on main as soon as checks pass without any manual merge step. The branch is deleted after merge.

## Issues

- #79 — Enable automerge and branch deletion on Lachesis-created PRs