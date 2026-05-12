---
id: "004"
title: "Google OAuth / MCP OAuth 2.1 authentication"
status: "done"
github_issue: null
created: "2026-05-12"
updated: "2026-05-12"
---

## Why

Lachesis is a hosted server. Claude.ai needs to authenticate before it can call the tools. MCP OAuth 2.1 is the standard for MCP server auth; Google OAuth is the identity provider.

## What

Google OAuth flow for single-user auth (email allowlisted via `ALLOWED_EMAIL` env var). MCP OAuth 2.1 endpoints (`/oauth/authorize`, `/oauth/token`, `/oauth/callback`). In-memory state stores for auth codes, sessions, and refresh tokens. Bearer token validated on every tool call.

## Issues
