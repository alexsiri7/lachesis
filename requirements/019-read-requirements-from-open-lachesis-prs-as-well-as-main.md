---
created: '2026-05-24'
github_issue: 80
id: 019
status: idea
title: Read requirements from open Lachesis PRs as well as main
updated: '2026-05-24'
---

## Why

Lachesis creates requirements via PRs when branch protection is active, but then immediately cannot see those requirements until they are manually merged. This breaks the workflow: you cannot link issues to a requirement you just created, which is always the next step. The PR is a transport mechanism, not a reason for the requirement to be invisible.

## What

When listing or fetching requirements, Lachesis checks both main and any open PRs it created. A requirement in an open PR is visible and usable — it can be linked to issues, queried, and updated — before the PR is merged. Its status reflects that it is pending merge (e.g. a `pr_url` field is present). Once merged, it transitions to the normal state seamlessly.

## Issues

- #80 — Include requirements from open Lachesis PRs in list and get operations
- #81 — Allow link_issues to update requirements in open PR branches