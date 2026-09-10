---
created: '2026-09-10'
github_issue: 141
id: 029
status: idea
title: Requirement files must stay writable after the first write
updated: '2026-09-10'
---

## Why

Writes to a requirement file succeed once and then fail permanently with a 409 on that path.

Observed end to end on `alexsiri7/un-reminder` requirement 016 on 2026-09-10. `create_requirement` created the file and opened PR #289. The first `link_issues` succeeded and attached six issues. Every subsequent write to that path failed with `409 Conflict` on the GitHub Contents API — two `update_requirement` calls and a second `link_issues`. PR #289 then merged; `get_requirement` confirmed the file was on the default branch by returning `pr: null`; the writes still 409'd.

A 409 from the Contents API means the blob SHA supplied does not match what is currently at that path, so Lachesis is sending a stale SHA rather than re-reading before it writes.

This matters because growing a requirement mid-session is the normal shape of the work, not an edge case. In the session where this was found, a mascot feature was designed after the first six issues had been filed; three further issues were created and could not be linked, and the requirement's `what` could not be extended to describe them. The requirement file now silently understates what was actually decided, which is precisely the failure the requirements layer exists to prevent, and Archon acts on issues that its own requirement does not list.

Issue #109 records 409s on `link_issues` but attributes them to unmerged PR branches and prescribes waiting for the merge. That workaround was followed here and did not help, so either #109's diagnosis is wrong or this is a second defect presenting with the same status code.

## What

Every write to a requirement file succeeds, however many writes have already been made to it. `update_requirement` and `link_issues` work the same on a requirement's tenth write as on its first, whether the file is on an open Lachesis PR branch or on the default branch.

A requirement stays editable for its whole life. Design sessions grow a requirement as they go — issues get added, the `what` gets extended as decisions are made — and none of that is a special case.

When a write genuinely cannot proceed, the tool says why in terms the caller can act on, rather than surfacing a bare HTTP status. A conflict caused by a concurrent change is distinguishable from a conflict caused by Lachesis holding the wrong version.

Existing behaviour that must not regress: `link_issues` on a fresh requirement advances its status from draft to idea, and repeated calls to `link_issues` are idempotent rather than duplicating entries in the Issues section.

## Issues

- #141 — Requirement file writes 409 permanently after the first successful write
- #142 — Re-examine #109: its diagnosis and workaround do not match observed behaviour