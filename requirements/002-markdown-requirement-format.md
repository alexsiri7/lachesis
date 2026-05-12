---
id: "002"
title: "Markdown-native requirement file format"
status: "done"
github_issue: null
created: "2026-05-12"
updated: "2026-05-12"
---

## Why

Requirements stored as Markdown files inside the repo are versioned alongside the code, human-readable without tooling, and editable by hand when needed.

## What

Each requirement is a `.md` file in `requirements/` with YAML frontmatter (`id`, `title`, `status`, `github_issue`, `created`, `updated`) and three sections: `## Why`, `## What`, and `## Issues`. IDs are zero-padded 3-digit integers. Filenames are slugified from the title (lowercase, spaces → hyphens, non-alphanumeric stripped). `create_requirement` creates the folder via GitHub API if it doesn't exist.

## Issues
