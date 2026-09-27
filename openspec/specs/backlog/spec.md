# Backlog

## Purpose

The backlog tells the autonomous worker what to build next. Order is set by the author through relative placement, dependencies constrain what is possible, and readiness marks what the author has finished thinking about. Priority labels and urgency grades are deliberately absent: with one author, they stop carrying information.

## Requirements

### Requirement: Each repository has one ordered backlog

The open issues of a repository SHALL form a single ordered backlog. Reading it SHALL return every open issue with its position, its ready flag and its interrupt flag, with interrupt-marked issues first.

#### Scenario: Reading the backlog
- GIVEN a repository with four open issues, one marked interrupt
- WHEN its backlog is read
- THEN the interrupt-marked issue is listed first
- AND every entry shows its position, ready flag and interrupt flag

### Requirement: Order is set relatively

The author SHALL move an issue only by placing it immediately before or immediately after another named open issue, never by absolute rank. Exactly one of before or after SHALL be given. The result SHALL be the reordered backlog.

#### Scenario: Move after
- GIVEN a backlog 10, 11, 12
- WHEN 10 is moved after 12
- THEN the backlog reads 11, 12, 10

#### Scenario: Both anchors given
- GIVEN any backlog
- WHEN a move names both a before and an after issue
- THEN the move is refused

### Requirement: Work is eligible unless parked

An issue SHALL be ready for autonomous pickup from the moment it is created. The author SHALL be able to park an issue and unpark it later. Parking SHALL mean the author has not finished deciding; sequencing SHALL be expressed with dependencies, not parking.

#### Scenario: Park and unpark
- GIVEN a ready issue
- WHEN it is parked
- THEN it is skipped for autonomous pickup
- AND when it is unparked it is eligible again at its original position

### Requirement: One interrupt marker

The system SHALL offer a single interrupt flag that puts an issue ahead of the order, and SHALL NOT offer graded urgency levels.

#### Scenario: Interrupt wins over position
- GIVEN issue 30 is last in the backlog and ready
- WHEN it is marked interrupt
- THEN it is the next issue offered for pickup, provided it is unblocked

### Requirement: The system says what to work on next

Asking for the next issue SHALL return the highest-ordered issue that is ready, has no open blocker, is not already in progress and is not waiting on the author, with an interrupt-marked issue winning over position. The repository SHALL be optional; without one, every registered repository SHALL be considered. When nothing qualifies, the answer SHALL say whether everything ready is blocked, everything is parked, everything eligible is waiting on the author, or the backlog is empty.

#### Scenario: Everything blocked
- GIVEN every ready issue has an open blocker
- WHEN the next issue is requested
- THEN no issue is returned
- AND the result says everything ready is blocked

#### Scenario: Skip work already in progress
- GIVEN the top issue already has an open pull request
- WHEN the next issue is requested
- THEN the following eligible issue is returned

#### Scenario: Waiting on the author
- GIVEN the top ready issue is labelled needs-author
- WHEN the next issue is requested
- THEN the following eligible issue is returned
- AND once the question is answered the waiting issue is eligible again at its original position

