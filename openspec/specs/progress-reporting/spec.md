# Progress Reporting

## Purpose

Progress reporting answers what happened to the work: what landed, what was dropped, what stalled and what went wrong. It is read from GitHub rather than trusted from hand-kept fields, and it surfaces the failures that otherwise produce no signal. The unit of work is a spec change in a repository that uses specs, and a requirement file in one that does not.

## Requirements

### Requirement: Status is derived from the work

For every unit of work, the system SHALL derive a status from its issues and their pull requests: "done" when every issue closed with a merged pull request, "in-progress" when work has started but not finished, and none when it has no issues. For a spec change, its issues SHALL be those whose reference block names it; for a requirement file, those linked to it. The system SHALL say where the derived status came from: the issues, no issues, or GitHub being unreadable, with the cause.

#### Scenario: Spec change fully built
- GIVEN merged spec change #152
- AND three issues naming #152, all closed with merged pull requests
- WHEN progress is reported
- THEN spec change #152 is "done"

#### Scenario: Requirement file shipped but never updated
- GIVEN a requirement file recorded as "idea"
- AND all its linked issues closed with merged pull requests
- WHEN it is listed
- THEN its derived status is "done"

### Requirement: Disagreement is reported, and author intent wins

When a requirement file's recorded status and its derived status differ, the system SHALL report both and flag it as diverged. Derivation SHALL NOT override a status the author set to express intent, such as cancelled or superseded. Spec changes carry no recorded status and SHALL NOT be reported as diverged.

#### Scenario: Diverged requirement file
- GIVEN recorded status "idea" and derived status "done"
- WHEN the requirement is listed
- THEN both values are shown and it is flagged as diverged

### Requirement: Dropped work is recognised

A spec change SHALL be reported as dropped when its pull request closed without merging, or when every issue naming it closed without a merged pull request. A requirement file SHALL be reported as dropped when cancelled or superseded.

#### Scenario: Spec change abandoned
- GIVEN a spec change whose pull request was closed unmerged
- WHEN progress is reported
- THEN it is in the dropped group

### Requirement: Activity and stalling are visible

Every unit of work SHALL carry the time its pull request or issues last moved, not the time a file was edited. It SHALL be reported as stalled when nothing has moved for a configurable number of days, defaulting to 14, unless it is done, dropped, or blocked by something that is itself still moving.

#### Scenario: Blocked behind stalled work
- GIVEN unit A is blocked by unit B
- AND neither has moved for 20 days
- WHEN progress is reported with the default threshold
- THEN both A and B are reported as stalled

### Requirement: Silent failures are named

The system SHALL report explicitly: issues closed with no merged pull request; a pull request failing checks, with since when; all issues closed while a requirement file is recorded in progress; and a unit with no issues, distinguishing one awaiting its own pull request from one that never had any.

#### Scenario: Merged spec change with no issues
- GIVEN a spec change that merged ten days ago
- AND no issue names it
- WHEN progress is reported
- THEN it is flagged as having no issues

### Requirement: Spec gaps are visible per capability

The system SHALL report each capability's open spec-gap issues, so that the distance between a spec and the code is visible without reading every issue.

#### Scenario: Audit results
- GIVEN three open spec-gap issues against the backlog capability
- WHEN progress is reported
- THEN the backlog capability shows three open spec gaps

### Requirement: One call answers the question

A single cross-repository call SHALL place every unit of work in exactly one group, tested in this order: dropped, awaiting merge, landed, needs attention, unreadable, never linked, stalled, diverged, in flight. Every group SHALL be present even when empty, and the summary SHALL give each group's count, counts by status, and open spec gaps by repository and capability.

#### Scenario: Morning check
- GIVEN work across three registered repositories, some using specs and some not
- WHEN the dashboard is requested once
- THEN it shows what landed, what was dropped, what stalled, what needs attention and the open spec gaps without further calls

### Requirement: Partial results are honest

When GitHub cannot be read for part of the request, the system SHALL name the affected repositories and the cause (rate limited, inaccessible or transient), SHALL mark any count that may be short as a lower bound, and SHALL report unreadable units separately from units whose work has gone wrong.

#### Scenario: Rate limited on one repository
- GIVEN one registered repository is rate limited
- WHEN the dashboard is requested
- THEN that repository is named with cause "rate limited"
- AND the counts are identified as lower bounds

### Requirement: A web view shows the same picture

The system SHALL offer a web page showing the same cross-repository summary as the dashboard call, grouped by repository and status, with each unit's title, status and number of issues. A visitor who is not signed in SHALL be prompted to connect before any data is shown.

#### Scenario: Signed-out visit
- GIVEN a visitor who is not signed in
- WHEN they open the web view
- THEN they are asked to connect and no data is shown
