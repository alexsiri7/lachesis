# Requirement Lifecycle

## Purpose

In a repository that does not yet use specs, a requirement file records a piece of behaviour the author wants, and why; the GitHub issues linked to it carry the implementation. This capability defines what a requirement file contains and how it moves through its life. Repositories that use specs follow spec-changes instead.

## Requirements

### Requirement: A requirement states intent, not implementation

Every requirement SHALL carry an identifier, a title, a why (the motivation or problem), a what (the desired outcome), a status, creation and update dates, and the list of issues linked to it. The what SHALL describe user-visible behaviour and SHALL NOT prescribe implementation steps, which belong in issues.

#### Scenario: Creating a requirement
- GIVEN a registered repository
- WHEN a requirement is created with a title, why and what
- THEN it is stored in the repository as described in repository-writes
- AND it has status "draft" and no linked issues

### Requirement: A requirement is a draft until it has issues

A requirement with no linked issues SHALL have status "draft". Linking issues to a draft for the first time SHALL advance it to "idea".

#### Scenario: First link
- GIVEN a draft requirement
- WHEN issues are linked to it
- THEN its status becomes "idea"
- AND the issues appear in its Issues section with their numbers and titles

### Requirement: Linking is idempotent

Linking an issue that is already linked SHALL leave the requirement unchanged and SHALL NOT duplicate the entry.

#### Scenario: Link the same issue twice
- GIVEN issue 12 is linked to a requirement
- WHEN issue 12 is linked again alongside new issue 13
- THEN the Issues section lists 12 once and 13 once

### Requirement: A requirement stays editable for its whole life

Every field of a requirement SHALL be updatable at any point in its life, including after issues are linked and after its pull request has merged. Growing a requirement during a design session SHALL be treated as normal use.

#### Scenario: Extending the what after linking
- GIVEN a requirement with linked issues
- WHEN its what is extended and further issues are linked
- THEN both changes are recorded

### Requirement: Statuses are a fixed canonical set

Status SHALL be exactly one of: draft, idea, planned, in-progress, done, cancelled, superseded. Each status SHALL have exactly one spelling. Records using a variant spelling SHALL be read as the canonical value.

#### Scenario: Variant spelling in an old record
- GIVEN a requirement file whose status is "in_progress"
- WHEN it is read or counted
- THEN it is reported and counted as "in-progress"

### Requirement: Dropping work is recorded, not deleted

Setting a requirement to "cancelled" SHALL require a reason. Setting it to "superseded" SHALL require what replaces it: either another requirement, as an identifier and a repository that defaults to the requirement's own, or a spec change, as a pull request number and a repository. Cancelled and superseded requirements SHALL be kept, SHALL be left out of listings by default, and SHALL be returned when inactive requirements are requested.

#### Scenario: Cancel without a reason
- GIVEN an active requirement
- WHEN its status is set to "cancelled" with no reason
- THEN the update is refused and the caller is told a reason is required

#### Scenario: Supersede across repositories
- GIVEN requirement "012" in repository A
- WHEN it is superseded by requirement "030" in repository B
- THEN it records "030" in repository B as its replacement
- AND it no longer appears in default listings of repository A

#### Scenario: Superseded by a spec
- GIVEN a repository that has just adopted specs
- WHEN its old requirements are superseded by the spec change that introduced its specs
- THEN each records that pull request as its replacement

### Requirement: Finished work cannot be deleted

The system SHALL refuse to delete a requirement whose status is "done". Requirements in other states MAY be deleted.

#### Scenario: Delete a done requirement
- GIVEN a requirement with status "done"
- WHEN deletion is requested
- THEN it is refused and the requirement is unchanged
