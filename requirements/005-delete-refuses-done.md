---
id: "005"
title: "delete_requirement refuses done status"
status: "superseded"
superseded_by:
  repo: "alexsiri7/lachesis"
  spec_change: 156
github_issue: 7
created: "2026-05-12"
updated: "2026-09-26"
---

## Why

A `done` requirement represents code that was actually written. Deleting the file doesn't delete the code — it creates an inconsistency where work exists without a corresponding requirement. Removal of implemented features requires a new requirement describing the removal.

## What

`delete_requirement` checks the status field before deleting. If status is `done`, it returns an error explaining the constraint. The tool description documents this behavior.

## Issues

- #7 — [Tool] delete_requirement
