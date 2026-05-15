---
created: '2026-05-15'
github_issue: null
id: '013'
status: idea
title: Requirements must be paired with tasks to be actionable
updated: '2026-05-15'
---

## Why

A requirement with no linked issues is just a note — it creates no work and has no path to completion. Currently nothing in lachesis prevents writing tasks disguised as requirements, or leaving requirements permanently unactioned. This undermines the tool's core purpose.

## What

Lachesis should enforce and communicate the distinction between requirements and tasks. A requirement captures intent and desired behaviour (why + what); a task/issue captures concrete implementation work. Requirements should never describe implementation steps. Lachesis should warn when a requirement has no linked issues, and the create_requirement flow should make clear that issues are the next step. Consider a draft status for requirements not yet paired with issues.

## Issues

_None yet._