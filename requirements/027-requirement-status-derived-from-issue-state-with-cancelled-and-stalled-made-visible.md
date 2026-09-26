---
created: '2026-09-07'
github_issue: 115
id: '027'
status: superseded
superseded_by:
  repo: alexsiri7/lachesis
  spec_change: 156
title: Requirement status derived from issue state, with cancelled and stalled made
  visible
updated: '2026-09-26'
---

## Why

Requirement status cannot currently be trusted, which makes the backlog unreadable to anyone other than the person who filed each item — and unreadable to them too after a few weeks.

Four concrete faults. Status is hand-maintained and lags reality: requirement 030 in word-coach-annie reports as an unlinked draft while its data-model issue is closed and marked done, so the work shipped and the record did not move. There is no way to express that a requirement was rejected, so 17 drafts sit in a state that means either "not started yet" or "decided against" with no way to tell which. Staleness is invisible, because listings carry no activity timestamps and computing them means fetching all 141 requirements individually. And the summary reports `in_progress` and `in-progress` as separate counts, so the same state is split across two keys and any total is quietly wrong.

The consequence is that the states most needing attention are the ones producing no signal. Work that stalled, was abandoned, or closed without shipping generates no events, so it never surfaces — while healthy work generates plenty. The backlog reports loudly on what is fine and says nothing about what is broken.

## What

**Status is derived, not asserted**

A requirement's progress is computed from the state of its linked issues and their PRs, rather than read from a hand-maintained field. A requirement whose issues have all closed with merged PRs reports as done whether or not anyone remembered to update it.

Where the recorded status and the derived one disagree, both are reported. The disagreement is itself the useful signal: it means either the bookkeeping has fallen behind the work, or issues were closed without being built.

Author-set intent — that a requirement is cancelled, or deliberately parked — is never overridden by derivation. Derivation answers what happened to the work; the author answers whether the work is still wanted.

**Cancelled and superseded become expressible**

The lifecycle gains states for a requirement the author has decided against, and for one replaced by another, recording which. Today a rejected requirement is indistinguishable from an untouched draft, so the backlog cannot be read: nobody can tell what is waiting from what is dead.

Cancelled and superseded requirements are excluded from active listings by default and retrievable on request. They are not deleted — the record of having considered and rejected something is worth keeping.

**Stalled is reported, not inferred**

Every requirement carries the time of its last real activity, meaning movement on its issues or PRs rather than an edit to its own file. Listings expose this, so requirements that have not moved can be identified without fetching each one in turn.

A requirement is reported as stalled when nothing has moved for a configurable period and it is neither done, cancelled, nor blocked by something that explains the silence. Being blocked is not stalling; being blocked behind something itself stalled is.

**Silent failure states are surfaced**

Certain states look like progress but are not, and each is reported explicitly rather than left for the reader to spot:

- Issues closed with no merged PR — usually abandoned, currently indistinguishable from completed
- A PR open and failing checks for an extended period
- All issues closed while the requirement still reports as in progress
- A requirement with no linked issues, distinguishing those awaiting an unmerged PR from those genuinely never linked

**Status values are canonical**

A given status has exactly one representation. Summaries that count by status count each state once. Existing records using variant spellings are normalised.

**One call answers the question**

"Which requirements are done, which were cancelled, and which have stalled" is answerable from a single cross-repo call, without per-requirement follow-ups.

## Issues

- #115 — Lifecycle states for cancelled and superseded, and canonical status values
- #116 — Derive requirement status from issues and PRs, with activity timestamps
- #117 — Dashboard: landed, dropped, stalled and needs-attention in one call