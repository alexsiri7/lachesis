---
created: '2026-09-07'
github_issue: 118
id: 028
status: idea
title: Ordered backlog and a ready gate for autonomous pickup
updated: '2026-09-07'
---

## Why

Archon picks up work autonomously overnight with no way to know which issue matters most. Dependencies constrain what is possible but say nothing about preference: where several issues are unblocked, selection is arbitrary, and the author discovers the following morning that the night went on something that could have waited.

The more expensive failure is not order but readiness. An autonomous worker given a vague issue does not stop and ask — it builds something plausible and wrong, and the cost lands on the author as review and rework. Filing an issue and finishing its specification are separate moments, sometimes days apart, and nothing currently marks the boundary. A gate makes the boundary explicit and makes "I have not finished thinking about this" a state the system understands.

Conventional priority labels are rejected on purpose. They work where competing stakeholders negotiate over a shared queue and the label records the outcome. Here there is one author, so a label records only a passing mood and never gets revisited, and within months everything is high priority and the field carries no information.

## What

**An explicit order**

Issues in a repo hold a position in a single ordered backlog. The author can move an issue to a position relative to another, and can ask what the worker should pick up next.

Order is relative rather than absolute. Placing an issue requires saying what it should come before or after, which keeps the ordering meaningful — a priority label can be applied without comparison and so drifts until everything shares the top band.

**Selection**

Asking what to work on next returns the highest-ordered issue that is not blocked by an open dependency. When nothing qualifies, the answer says why — everything blocked, or the backlog empty — rather than returning empty, since those call for different responses from the author.

**Interrupts**

A single marker lets the author put an issue at the front regardless of order, for genuine interrupts. Exactly one such marker exists. Graded urgency levels are deliberately not offered: with a single author and an autonomous worker, grades collapse into everything being urgent, and the grooming needed to prevent that is effort spent maintaining a signal that has already stopped meaning anything.

**Work is eligible by default**

An issue is available for autonomous pickup as soon as it is created. Filing an issue is already the author's decision that the work is wanted; requiring a second confirmation afterwards moves review back onto the author, which is the cost the autonomous workflow exists to remove.

The author can park an issue — marking it as captured but not to be built yet — and unpark it later. Parking is the deliberate exception rather than the default state, because it describes something rare: an idea recorded mid-session that is knowingly unfinished.

Parking is not how work is sequenced. An issue that should not be built yet because it depends on something unbuilt is blocked, and dependencies already express that. Parking means the author has not decided, which is a different thing from the author having decided it comes later.
## Issues

- #118 — Backlog ordering and ready flag on issues
- #119 — next_issue — what should the worker pick up