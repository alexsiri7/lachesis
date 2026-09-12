---
created: '2026-08-30'
github_issue: 143
id: '024'
status: idea
title: link_issues fails with 409 when the target requirement lives on an open PR
  branch
updated: '2026-09-12'
---

## Why

Reproduced live: calling `link_issues` on a requirement (word-coach-annie requirement 029) that lives only on its own open Lachesis PR (#1049, not yet merged to main) throws a 409 Conflict from the GitHub Contents API, because `link_issues` tries to write to `requirements/029-*.md` on `main`, where that file doesn't exist yet. This blocks the standard workflow (`create_requirement` → `create_github_issues` → `link_issues`) from ever completing in a single session for a brand-new requirement — the requirement is stuck in `draft` until its own PR merges, even though issues have already been created and are ready to attach.

## What

`link_issues` should detect which branch a requirement's file actually lives on — `main`, or its own still-open Lachesis PR branch — and commit the updated file (with linked issues, and the status transition from `draft` to `idea`) to that branch. It should not assume the requirement lives on `main`.

This should be consistent with the read-side fix already shipped for `list_requirements`/`get_requirement` (requirement 019 in this repo): once a requirement's branch is known and resolvable for reads, the same resolution should be used for the write path in `link_issues`.

## Issues

- #143 — link_issues still 409s when the requirement's PR is unmerged — #109's fix did not land