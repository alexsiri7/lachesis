---
id: "006"
title: "ID and filename auto-assignment"
status: "superseded"
superseded_by:
  repo: "alexsiri7/lachesis"
  spec_change: 156
github_issue: 5
created: "2026-05-12"
updated: "2026-09-26"
---

## Why

Consistent, predictable IDs and filenames make requirements easier to reference in conversation and in GitHub issues. Manual assignment would introduce gaps and collisions.

## What

`create_requirement` lists existing files, finds the highest existing ID, and assigns the next. Filename slug is derived from title (lowercase, spaces → hyphens, non-alphanumeric stripped). Slug collisions get `-2`, `-3` suffixes.

## Issues

- #5 — [Tool] create_requirement
