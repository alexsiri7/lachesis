---
created: '2026-05-24'
github_issue: null
id: '014'
status: draft
title: OAuth tokens must survive app restarts
updated: '2026-05-24'
---

## Why

The Lachesis MCP server loses all OAuth tokens on every restart. Since the app restarts frequently (deploys, crashes, scaling events), users are constantly forced to re-authenticate, making the MCP unusable in practice.

## What

After an app restart, the Lachesis MCP server resumes serving requests using previously-obtained OAuth tokens without requiring the user to re-authenticate, as long as those tokens have not expired or been revoked. Refreshing an expired access token using a stored refresh token happens automatically and transparently.

## Issues

_None yet._