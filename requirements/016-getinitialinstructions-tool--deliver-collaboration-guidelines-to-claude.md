---
created: '2026-05-24'
github_issue: 74
id: '016'
status: superseded
superseded_by:
  repo: alexsiri7/lachesis
  spec_change: 156
title: get_initial_instructions tool — deliver collaboration guidelines to Claude
updated: '2026-09-26'
---

## Why

Nothing currently tells Claude (or Claude Code via Archon) that creating a requirement is only the first step. Without the follow-up issues and link, the requirement stays in draft and Archon has nothing actionable to pick up. The knowledge of the correct workflow needs to travel with the MCP server, not live in an external system prompt that has to be kept in sync.

## What

A `get_initial_instructions` tool exists on the Lachesis MCP server. It returns static guidelines explaining how Claude should work with Lachesis: always follow `create_requirement` with `create_github_issues` and `link_issues` so every requirement reaches `idea` status with actionable tasks attached. A requirement without linked issues is a draft that nothing will act on. The guidelines live in Lachesis and are picked up automatically by any integration — Claude Code, Archon, or otherwise — without needing to be maintained externally.

## Issues

- #74 — Implement get_initial_instructions MCP tool
- #75 — Document get_initial_instructions in Lachesis README