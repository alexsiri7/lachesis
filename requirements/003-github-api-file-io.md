---
id: "003"
title: "GitHub API file I/O (no local disk)"
status: "done"
github_issue: null
created: "2026-05-12"
updated: "2026-05-12"
---

## Why

The server is deployed to Railway with no persistent local filesystem. All requirement files must be read and written directly via the GitHub REST API using a server-side PAT.

## What

`github.py` module — GitHub API client authenticated via `GITHUB_PAT` env var with `repo` scope. All file reads use `GET /repos/{owner}/{repo}/contents/{path}`. All writes use `PUT` with base64-encoded content and the current file's `sha`. Folder creation uses the same PUT endpoint with a placeholder `.gitkeep` file.

## Issues
