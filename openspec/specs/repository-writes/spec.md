# Repository Writes

## Purpose

Lachesis keeps everything it manages as markdown files in the target repository, read and written only through the GitHub API. This capability defines how those files are created, found and updated, so that a caller can treat a file as one stable thing for its whole life regardless of which branch it currently sits on.

## Requirements

### Requirement: GitHub is the only store

The system SHALL read and write managed files only through the GitHub API and SHALL NOT rely on local disk state between calls.

#### Scenario: Restart loses nothing
- GIVEN a requirement has been created
- WHEN the server restarts
- THEN reading that requirement returns the same content as before the restart

### Requirement: Writes respect branch protection

When the default branch is protected, the system SHALL make every write through a pull request it opens, and SHALL enable automerge on that pull request so that it merges once the repository's required checks pass, with no action from the author.

#### Scenario: Protected default branch
- GIVEN the default branch rejects direct pushes
- WHEN a requirement is created
- THEN the file is committed to a new Lachesis branch
- AND a pull request is opened for it with automerge enabled
- AND the caller is told the pull request number

#### Scenario: Status update merges by itself
- GIVEN a registered repository whose default branch is protected
- WHEN a requirement's status is updated to done
- THEN the pull request Lachesis opens for it merges once its required checks pass
- AND the author is never asked to merge it

### Requirement: A Lachesis pull request that cannot merge by itself is a Lachesis fault

When the system cannot enable automerge on a pull request it opens, it SHALL tell the caller why in terms they can act on. Any open Lachesis pull request that cannot merge without a human SHALL be reported as a Lachesis fault naming the reason, and SHALL NOT be reported as waiting on the author.

#### Scenario: Automerge refused
- GIVEN a registered repository where automerge cannot be enabled
- WHEN Lachesis opens a pull request there
- THEN the caller is told automerge could not be enabled and why
- AND the dashboard reports the pull request as a Lachesis fault, not as waiting on the author

### Requirement: Pending files are fully usable

A file that exists only on an open Lachesis pull request SHALL be readable, listable and writable exactly as if it were on the default branch. Every response for such a file SHALL identify the pull request it was read from, and SHALL report no pull request once the file is on the default branch.

#### Scenario: Create then link immediately
- GIVEN a requirement was just created and its pull request is still open
- WHEN issues are linked to it
- THEN the link succeeds
- AND reading the requirement shows the linked issues and the open pull request

#### Scenario: After merge
- GIVEN a requirement's pull request has merged
- WHEN the requirement is read
- THEN it is read from the default branch and reports no pull request

### Requirement: Writes land where the file lives

Every write SHALL target the branch holding the file's latest version at the moment of writing, determined immediately before the write rather than from an earlier read. A pull request merging between a read and a write SHALL NOT cause the write to fail.

#### Scenario: Merge races the write
- GIVEN a requirement is on an open pull request
- AND that pull request merges after the caller's last read
- WHEN the caller links issues to the requirement
- THEN the link is written to the default branch and succeeds

### Requirement: Files stay writable

A file SHALL accept any number of writes over its life. The system SHALL use the file's current version for every write and SHALL NOT reuse a version from an earlier write.

#### Scenario: Tenth write
- GIVEN a requirement has already been written nine times, on its pull request branch, the default branch, or both
- WHEN it is updated again
- THEN the update succeeds

### Requirement: Failed writes are explained

When a write cannot proceed, the system SHALL tell the caller why in terms they can act on, and SHALL NOT surface a bare HTTP status. A conflict caused by someone else changing the file SHALL be reported differently from a conflict caused by Lachesis holding a stale version.

#### Scenario: Concurrent edit
- GIVEN another writer changed the file after the system read it
- WHEN the system's write is rejected
- THEN the caller is told the file changed underneath them and what changed

### Requirement: Identifiers are unique and stable

The system SHALL assign each new requirement an identifier not used by any requirement file on the default branch or on any open pull request branch. Identifiers SHALL be zero-padded three-digit strings and SHALL NOT be stored or returned as integers.

#### Scenario: Several created before any merge
- GIVEN the highest existing identifier is "029"
- AND no pull requests have merged since
- WHEN three requirements are created one after another
- THEN they receive "030", "031" and "032"

#### Scenario: Identifier survives a round trip
- GIVEN requirement "030" exists
- WHEN it is read, updated and read again
- THEN its identifier is still the string "030"
