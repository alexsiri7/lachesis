# Issues

## Purpose

GitHub issues are the task layer: they describe how a requirement's behaviour gets built, and they are what the autonomous worker picks up. This capability covers creating, reading and editing issues, and reading the pull requests that implement them.

## Requirements

### Requirement: Issues are created ready and last

The system SHALL create one or more issues in a single call. Each new issue SHALL be ready for autonomous pickup and placed last in the repository's backlog, unless the caller marks it not ready, in which case it SHALL be created parked.

#### Scenario: Batch creation
- GIVEN a backlog of five issues
- WHEN three issues are created, the second marked not ready
- THEN they occupy positions six, seven and eight in creation order
- AND the second is parked and the others are ready

### Requirement: Dependencies declared at creation are honoured

When an issue is created with issues it is blocked by, the system SHALL record those dependencies as described in dependencies, and SHALL NOT silently ignore them.

#### Scenario: Blocked at birth
- GIVEN issue 40 exists
- WHEN issue 41 is created blocked by 40
- THEN reading the dependencies of 41 shows it is blocked by 40

### Requirement: Issues say what they implement

In a repository that uses specs, every issue created SHALL name either the spec change it implements, by pull request number, or be marked as a spec gap: work that brings the code in line with the spec as it already stands, such as an audit finding or a bug. Both kinds SHALL name the capability and SHALL accept the requirement within it. The system SHALL write this reference into the issue body when the issue is created, as a block at the end of the body in this form:

    ---
    Spec change: #152
    Capability: backlog
    Requirement: The system says what to work on next

A spec gap SHALL carry `Spec gap: yes` in place of the spec change line and SHALL be labelled `spec-gap`. The reference SHALL be complete at creation, so no later write is needed to connect an issue to its work.

#### Scenario: Implementing a spec change
- GIVEN open spec change #152
- WHEN an issue is created naming #152, capability backlog and a requirement
- THEN the issue body ends with the reference block naming all three

#### Scenario: Audit finding
- GIVEN an audit finds the code breaks an existing requirement
- WHEN an issue is created as a spec gap for that capability and requirement
- THEN it is labelled spec-gap and its reference block says so

#### Scenario: No reference
- GIVEN a repository that uses specs
- WHEN an issue is created naming neither a spec change nor a spec gap
- THEN creation is refused and the caller is told what is missing

### Requirement: Issues can be read and edited

The system SHALL return an issue's title, body, state, assignees, labels and linked pull request. It SHALL allow an issue's title, body and state (open or closed) to be changed, and SHALL return the updated issue's number, title and URL.

#### Scenario: Closing an issue
- GIVEN an open issue
- WHEN its state is set to closed
- THEN reading it reports it as closed

### Requirement: Pull requests can be read

The system SHALL return any pull request in a registered repository, whether or not Lachesis opened it, with its number, title, URL, state, whether it merged and when, whether it is mergeable and its mergeable state, its head and base branches, and whether it is a draft. Asking for a pull request that does not exist SHALL return a not-found result rather than an error.

#### Scenario: Closed without merging
- GIVEN a pull request that was closed unmerged
- WHEN it is read
- THEN it reports state "closed" and merged false

#### Scenario: Unknown number
- GIVEN no pull request 9999 exists
- WHEN it is requested
- THEN the result says it was not found
