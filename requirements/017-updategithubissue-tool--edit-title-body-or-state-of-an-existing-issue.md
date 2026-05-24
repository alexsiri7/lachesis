---
created: '2026-05-24'
github_issue: 76
id: '017'
status: idea
title: update_github_issue tool — edit title, body, or state of an existing issue
updated: '2026-05-24'
---

## Why

Currently there is no way to edit a GitHub issue once created via Lachesis. Mistakes in issue descriptions require manual correction in the GitHub UI, breaking the flow of working with Claude. The immediate trigger was needing to correct a story object type misclassification in issue #715 in word-coach-annie.

## What

An `update_github_issue(repo, issueNumber, fields)` tool exists on the Lachesis MCP server. It accepts any combination of title, body, and state (open/closed) and applies them to the specified issue. Claude can correct a poorly worded issue, close one that's been superseded, or amend the description without leaving the MCP context.

## Issues

- #76 — Implement update_github_issue MCP tool