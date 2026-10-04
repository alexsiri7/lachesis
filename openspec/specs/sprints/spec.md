# Sprints

## Purpose

The Claude allowance resets weekly, so the factory works in weekly sprints aligned with it. It estimates work in tokens, plans what fits, measures everything and reports once a week. The sprint is the factory's own governor: a regular point to stop, measure and re-plan without the author acting as the brake. Carryover is information, not failure.

## Requirements

### Requirement: Sprints follow the allowance

Each sprint SHALL start when the weekly Claude allowance resets and end at the next reset. The system SHALL return the current sprint with its dates, plan, budget and what has been spent.

#### Scenario: Reading the sprint
- GIVEN a sprint in progress
- WHEN the current sprint is read
- THEN its start, end, plan, budget and spending are returned

### Requirement: Work is estimated from history

Every issue SHALL carry an estimate in tokens, made from the measured usage of past issues of the same kind and capability. Until there are five such past issues, the estimate SHALL be marked low confidence.

#### Scenario: Estimate from history
- GIVEN eight past implementation issues in the backlog capability
- WHEN a new implementation issue in that capability is estimated
- THEN its estimate is derived from their measured usage and marked normal confidence

### Requirement: The factory plans the sprint

At the start of each sprint the factory SHALL plan it: take the computed backlog in order and include issues until their estimates fill the sprint budget, the allowance and overflow cap less the reserve. The plan SHALL never wait on the author. When the author changes focus during a sprint, the plan SHALL be redone from the new order.

#### Scenario: Plan fills the budget
- GIVEN a sprint budget of 100 units and backlog estimates of 40, 40 and 30
- WHEN the sprint is planned
- THEN the first two issues are planned and the third is not

#### Scenario: Focus changes mid-sprint
- GIVEN a sprint in progress
- WHEN the author sets a new focus
- THEN the plan is redone from the new backlog order

### Requirement: The week is reported

At the end of each sprint the author SHALL receive one push notification and a report saying what was delivered and is available, what was spent on Claude and on overflow, how estimates compared with actual usage, and what carried over.

#### Scenario: Sprint ends
- GIVEN a sprint that delivered two milestones and carried over three issues
- WHEN it ends
- THEN exactly one push notification arrives
- AND the report lists the delivered work, the spending, estimate accuracy and the three carried-over issues

### Requirement: The factory reviews itself

At the end of each sprint the factory SHALL review its own misses, such as estimates far off, retries and rework, and record its findings as notes. A finding that needs a standing rule SHALL become a question to the author; any other finding SHALL become work in the backlog.

#### Scenario: Repeated estimate miss
- GIVEN implementation estimates in one capability were less than half of actual usage all sprint
- WHEN the sprint is reviewed
- THEN a finding note records the miss
