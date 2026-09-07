---
created: '2026-09-07'
github_issue: null
id: '025'
status: draft
title: 'Pull request visibility: PR state on requirements and a get_pull_request tool'
updated: '2026-09-07'
---

## Why

Lachesis writes requirement files onto PR branches and its own tooling behaves differently depending on whether those PRs have merged. `link_issues` fails with a 409 against a file on an open branch, and `get_requirement` and `update_requirement` resolve unreliably in the same situation. Yet nothing in the Lachesis surface reports PR state, so the only way to discover that a requirement is waiting on an unmerged branch is to attempt an operation and read the error, or to query the GitHub API separately.

This is an avoidable gap. Lachesis already holds an authenticated GitHub token and already opens the PRs in question, so it knows their numbers at creation time. Exposing their state is a read against a connection that exists rather than new infrastructure.

The cost of the gap is concrete and current. Four requirements across two repos are sitting in `draft` waiting on unmerged branches, and in one case a requirement's issues have already been implemented and closed while the requirement itself still reports as unlinked. Anyone working through the backlog has to guess which requirements are genuinely awaiting attention and which are merely waiting on a merge.

## What

**PR state on requirement listings**

When Lachesis returns a requirement — through `list_requirements`, `get_requirement` or the dashboard — a requirement whose file lives on a Lachesis-opened PR reports that PR's number and whether it is open, merged or closed unmerged.

This makes the two most common questions answerable without leaving Lachesis: is this requirement's file still on a branch, and can `link_issues` be expected to succeed against it yet.

A requirement whose file is on main reports no PR, which is the normal steady state and not a warning.

**Explicit PR lookup**

A caller can ask for the current state of any pull request in a tracked repo by number, and receive its title, state, whether it is merged, its mergeability, the state of its checks, and its branch. This covers the case of asking about implementation PRs opened by Archon, not just requirement PRs opened by Lachesis.

**Warnings that reflect why a requirement is stuck**

Today an unlinked requirement carries the same "no linked issues" warning whether the author simply has not linked issues yet or whether linking is impossible because the file is on an unmerged branch. These are different situations with different next actions, and the warning distinguishes them: a requirement awaiting an unmerged PR says so and names the PR.

**Dashboard**

The cross-repo dashboard surfaces requirements blocked behind an unmerged PR as a distinct group, so a stalled requirement PR is visible without inspecting each repo in turn.

## Issues

_None yet._