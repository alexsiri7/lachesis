---
id: "007"
title: "Railway deployment with Cloudflare DNS"
status: "superseded"
superseded_by:
  repo: "alexsiri7/lachesis"
  spec_change: 156
github_issue: null
created: "2026-05-12"
updated: "2026-09-26"
---

## Why

The server needs to be publicly accessible for Claude.ai to connect to it. Railway provides simple Python deployment; Cloudflare DNS maps `lachesis.interstellarai.net` to the Railway service.

## What

FastMCP app deployed to Railway. `MCP_BASE_URL` env var set to `https://lachesis.interstellarai.net`. Cloudflare DNS CNAME pointing to the Railway service URL. OAuth redirect URI registered in Google OAuth Console.

## Issues
