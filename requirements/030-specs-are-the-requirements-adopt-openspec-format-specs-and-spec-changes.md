---
created: '2026-09-26'
github_issue: 147
id: '030'
status: done
title: 'Specs are the requirements: adopt OpenSpec-format specs and spec changes'
updated: '2026-09-28'
---

## Why

Requirement files record changes, not what the project should be. After 29 of them, nothing in Lachesis states what Lachesis is supposed to do, so an audit means replaying a change log. The log also hides repetition: 019, 024 and 029 are the same rule ("a write lands where the file lives") restated after each fix fell short, and a single statement of desired behaviour would have shown one failing rule rather than three separate records.

Hand-kept status has failed in practice: 21 of Lachesis's 29 requirements report as diverged, recorded as "idea" while their issues are done. And every 409 bug in the requirements layer (#109, #141, #143) comes from link_issues making a second write to a file that may be on a moving branch.

A spec per capability, written as desired state and changed only through pull requests that carry the why, fixes all three: audits compare code to one document, status is read from the work instead of stored, and issues state what they implement when they are created, so there is no second write to race. The Rust port also needs exactly this: a complete statement of the behaviour it must reproduce.

## What

Each repository can hold its specification in OpenSpec's main-spec format under openspec/specs/: one file per capability, with a purpose and requirements that each carry Given/When/Then scenarios. The spec describes what the project should be, never a list of changes, and never status or progress.

In a repository with specs, work starts as a spec change: a pull request editing one or more capability files, with the why in its description, checked for format before it opens and editable until it merges. Issues state the spec change, capability and requirement they implement when they are created, or are marked as spec gaps when they bring the code in line with the spec as it already stands. Progress, dashboards and landing notifications work on spec changes the way they work on requirements today, and show open spec gaps per capability. Agent guidance describes whichever workflow applies to the repository.

Repositories without specs keep the requirement-file workflow unchanged, so Annie, Reli and the others move over one at a time. Lachesis itself adopts specs first: its full specification lands in the repository, is enforced by format validation in CI, and its old requirement files are superseded by it. The exact behaviour is the specification landed by this requirement's first issue.

## Issues

- #147 — Add Lachesis's OpenSpec specification and validate it in CI
- #148 — Detect repositories that use specs, and add tools to read specs
- #149 — Spec changes: propose and edit spec changes as pull requests, with format checking
- #150 — Issues state what they implement: spec-change references and spec gaps
- #151 — Switch spec repositories to the spec workflow and supersede Lachesis's old requirements
- #152 — Progress reporting on spec changes, with spec gaps per capability
- #153 — Landing notifications for spec changes