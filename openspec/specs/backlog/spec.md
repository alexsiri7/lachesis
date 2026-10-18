# Backlog

## Purpose

The backlog tells the autonomous worker what to build next. Its order is computed by the system, not set by hand, so the author is never on the critical path. The author steers by naming a focus and by the single interrupt marker; dependencies constrain what is possible. Work is ready from the moment it exists: readiness is never something the author grants, and nothing waits on the author except work blocked by a recorded question. Priority labels and urgency grades stay absent: the order itself carries the priority.

## Requirements

### Requirement: Each repository has one ordered backlog

The open issues of a repository SHALL form a single ordered backlog. Reading it SHALL return every open issue with its position, its tier, its ready flag and its interrupt flag.

#### Scenario: Reading the backlog
- GIVEN a repository with four open issues, one marked interrupt
- WHEN its backlog is read
- THEN the interrupt-marked issue is listed first
- AND every entry shows its position, tier, ready flag and interrupt flag

### Requirement: Order is computed

The system SHALL order the backlog by tier, highest first: interrupt; security (spec gaps found by a security audit or a security incident); in the author's focus; work implementing a spec change the author asked for, for two sprints after it merges; work implementing an active milestone, nearest-done milestone first; everything else. Within a tier, older issues SHALL come first.

#### Scenario: Security before milestone work
- GIVEN a ready milestone issue created yesterday and a ready security spec gap created today
- WHEN the backlog is read
- THEN the security spec gap comes first

#### Scenario: The author's request ranks high
- GIVEN a spec change the author asked for merged yesterday
- AND an older ready issue in the last tier
- WHEN the backlog is read
- THEN the issues implementing the author's spec change come before the older issue

### Requirement: Starving work rises a tier

An issue that has been ready and unblocked for two whole sprints without being picked up SHALL move up one tier, so no work starves.

#### Scenario: Starving work rises
- GIVEN a ready, unblocked issue in the last tier that has waited two whole sprints
- WHEN the backlog is read
- THEN the issue is in the tier above

### Requirement: No rank is set by hand

The system SHALL NOT offer a way to set an issue's rank by hand.

#### Scenario: No tool sets a rank
- WHEN the available tools are listed
- THEN none sets an issue's rank by hand

### Requirement: The author steers with focus

The author SHALL be able to set a focus on any number of repositories or milestones, and clear it. Setting or clearing a focus SHALL take effect on the next read of the backlog and SHALL never wait on any other decision.

#### Scenario: Focusing a milestone
- GIVEN two active milestones A and B with ready work
- WHEN the author sets focus on B
- THEN B's issues come before A's in the backlog

### Requirement: Work is eligible unless parked

An issue SHALL be ready for autonomous pickup from the moment it is created. An agent SHALL be able to park an issue it knows is unfinished and unpark it later. Parking SHALL NOT be how work waits for the author: what needs the author's decision SHALL be a recorded question blocking that issue. Sequencing SHALL be expressed with dependencies, not parking.

#### Scenario: Park and unpark
- GIVEN a ready issue
- WHEN it is parked
- THEN it is skipped for autonomous pickup
- AND when it is unparked it is eligible again in its computed place

#### Scenario: Waiting on a decision
- GIVEN an issue that needs a decision only the author can make
- WHEN an agent handles it
- THEN a question blocking it is recorded
- AND the issue is not parked

### Requirement: One interrupt marker

The system SHALL offer a single interrupt flag that puts an issue in the top tier, and SHALL NOT offer graded urgency levels.

#### Scenario: Interrupt wins over every tier
- GIVEN issue 30 is in the last tier and ready
- WHEN it is marked interrupt
- THEN it is the next issue offered for pickup, provided it is unblocked

### Requirement: The system says what to work on next

Asking for the next issue SHALL return the highest-ordered issue that is ready, has no open blocker, is not already in progress, is not waiting on the author and fits the current sprint's remaining budget as described in sprints. The repository SHALL be optional; without one, every registered repository SHALL be considered.

#### Scenario: Skip work already in progress
- GIVEN the top issue already has an open pull request
- WHEN the next issue is requested
- THEN the following eligible issue is returned

#### Scenario: Waiting on the author
- GIVEN the top ready issue is labelled needs-author
- WHEN the next issue is requested
- THEN the following eligible issue is returned
- AND once the question is answered the waiting issue is eligible again in its computed place

### Requirement: An empty answer says why

When no issue qualifies, the answer SHALL say whether everything ready is blocked, everything is parked, everything eligible is waiting on the author, the sprint budget is spent, spending is ahead of pace, or the backlog is empty.

#### Scenario: Everything blocked
- GIVEN every ready issue has an open blocker
- WHEN the next issue is requested
- THEN no issue is returned
- AND the result says everything ready is blocked

#### Scenario: Budget spent
- GIVEN the current sprint has no budget left outside the reserve
- WHEN the next issue is requested and the top issue is not security work
- THEN no issue is returned
- AND the result says the sprint budget is spent
