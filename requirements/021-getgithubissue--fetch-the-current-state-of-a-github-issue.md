---
created: '2026-05-26'
github_issue: null
id: '021'
status: draft
title: get_github_issue — fetch the current state of a GitHub issue
updated: '2026-05-26'
---

## Why

Currently there is no way to inspect an issue's current state via Lachesis. Verifying whether work is done requires the user to check GitHub manually and report back, breaking the flow of working with Claude.

## What

A `get_github_issue(repo, issueNumber)` tool exists on the Lachesis MCP server. It returns the issue's title, body, state (open/closed), assignees, labels, and linked PR if any. Claude can verify whether an issue is done, check what it says, or confirm a dependency is resolved without leaving the MCP context or asking the user to check manually.

## Issues

_None yet._