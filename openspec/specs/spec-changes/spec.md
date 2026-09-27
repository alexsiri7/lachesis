# Spec Changes

## Purpose

In a repository that uses specs, the spec is the single statement of what the project should be. Work starts as a spec change: a pull request that edits one or more capability files and carries the reason for the edit. The spec never records status or progress; those are read from the work itself.

## Requirements

### Requirement: A repository with specs uses spec changes

A registered repository whose default branch contains `openspec/specs/` SHALL be treated as using specs. In such a repository, creating a requirement file SHALL be refused with a message pointing the caller to spec changes. Repositories without specs SHALL keep the requirement-file workflow described in requirement-lifecycle.

#### Scenario: Old workflow in a spec repository
- GIVEN a repository whose default branch contains openspec/specs/
- WHEN a requirement is created
- THEN it is refused
- AND the caller is told to propose a spec change instead

### Requirement: Specs can be read

The system SHALL list a repository's capabilities with each one's name, purpose and number of requirements, and SHALL return any capability's full spec as it stands on the default branch. Reading a capability SHALL also list the open spec changes that touch it.

#### Scenario: Pending edit is visible
- GIVEN an open spec change editing the backlog capability
- WHEN the backlog capability is read
- THEN the default-branch spec is returned
- AND the open spec change is listed with its pull request number

### Requirement: A spec change is a pull request

The system SHALL create a spec change from a title, a why, and the complete new content of every capability it adds, edits or removes. It SHALL open one pull request for the change through repository-writes, put the why in the pull request description, and label it `spec-change`. The pull request number SHALL identify the spec change everywhere.

#### Scenario: Proposing a change to two capabilities
- GIVEN a repository that uses specs
- WHEN a spec change is proposed editing the backlog and issues capabilities
- THEN one pull request is opened containing both edits
- AND its description holds the why
- AND the caller receives its number

### Requirement: Spec changes are checked before they are opened

The system SHALL refuse a spec change whose content is malformed, and SHALL list every problem found. Each capability SHALL have a title, a Purpose section and a Requirements section; every requirement SHALL use SHALL or MUST and SHALL have at least one scenario.

#### Scenario: Requirement without a scenario
- GIVEN proposed content with a requirement that has no scenario
- WHEN the spec change is proposed
- THEN it is refused
- AND the response names the capability and requirement at fault

### Requirement: An open spec change stays editable

Until its pull request merges, the author SHALL be able to replace the content of any capability in a spec change, add or drop capabilities from it, and change its why. A merged spec change SHALL NOT be edited; further changes SHALL be new spec changes.

#### Scenario: Extending an open change
- GIVEN an open spec change editing the backlog capability
- WHEN the issues capability is added to it
- THEN the same pull request now edits both

### Requirement: Specs hold only desired behaviour

The system SHALL NOT write status, progress, issue numbers or dates into spec files.

#### Scenario: Work lands
- GIVEN every issue for a spec change has merged
- WHEN progress is reported
- THEN no spec file is modified

### Requirement: A spec change brings its own acceptance tests

When a spec change is proposed, the system SHALL open an issue labelled `acceptance-tests` for it, naming the spec change and the capabilities it touches. That issue's work SHALL be done on the spec change's own branch, so that the spec and its acceptance tests merge together, as described in delivery.

#### Scenario: Proposing opens the test issue
- GIVEN a repository that uses specs
- WHEN a spec change is proposed
- THEN an acceptance-tests issue is opened naming the spec change and its capabilities
- AND the caller receives its number with the spec change's
