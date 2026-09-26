---
created: '2026-05-14'
github_issue: 24
id: '011'
status: superseded
superseded_by:
  repo: alexsiri7/lachesis
  spec_change: 156
title: Supabase persistence layer (namespaced tables)
updated: '2026-09-26'
---

## Why

Lachesis needs persistent storage for user and registry data but has no DB. Using GitHub for this would expose private user data publicly. Namespacing tables in an existing Supabase instance avoids the cost of a new DB while keeping the architecture clean and multi-user ready.

## What

Introduce a Supabase integration using Alex's existing Supabase instance, with all lachesis tables namespaced with a `lachesis_` prefix (e.g. `lachesis_users`, `lachesis_repos`). Requirements themselves continue to live in GitHub. Supabase stores only user identity and registry data. The integration should be user-scoped from day one to support future multi-user use.

## Issues

- #24 — Supabase persistence layer (namespaced tables)