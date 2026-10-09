# Sprints

## Purpose

The primary Claude account's allowance resets weekly, so the factory works in weekly sprints aligned with it. It sizes work in Fibonacci points, learns from every finished issue how many tokens a point costs, plans as many points as the budget buys, measures everything and reviews the sprint once a week. The sprint is the factory's own governor: a regular point to stop, measure and re-plan without the author acting as the brake. Carryover is information, not failure.

## Requirements

### Requirement: Sprints follow the allowance

Each sprint SHALL start when the primary account's weekly allowance resets and end at its next reset. The system SHALL return the current sprint with its dates, plan, budget and what has been spent.

#### Scenario: Reading the sprint
- GIVEN a sprint in progress
- WHEN the current sprint is read
- THEN its start, end, plan, budget and spending are returned

### Requirement: Every issue is sized

Every issue SHALL carry a size in Fibonacci points: 1, 2, 3, 5, 8 or 13, given when it enters the backlog and changeable by the author. An issue judged larger than 13 SHALL NOT be planned; it SHALL be split into smaller issues first, and the split is itself work in the backlog.

#### Scenario: Issue sized on arrival
- GIVEN a new implementation issue enters the backlog
- WHEN it is triaged
- THEN it carries one of the sizes 1, 2, 3, 5, 8 or 13

#### Scenario: Too big to plan
- GIVEN an issue judged larger than 13 points
- WHEN the sprint is planned
- THEN it is not in the plan
- AND splitting it is

### Requirement: Points are priced from history

The system SHALL measure, from the issues closed in past sprints, the tokens a point costs for each kind of work and model, and SHALL estimate each issue in tokens as its size times that price. Until there are five such past issues, the price and every estimate made from it SHALL be marked low confidence, falling back to the price across all kinds.

#### Scenario: Estimate from points
- GIVEN past implementation issues on one model cost 2 units per point
- AND eight such issues have closed
- WHEN a new 5-point implementation issue is estimated for that model
- THEN its estimate is 10 units, normal confidence

### Requirement: The factory plans the sprint

At the start of each sprint the factory SHALL plan it: take the computed backlog in order, together with the due audits, and include work until its estimates fill the sprint budget, which is the factory share of every registered account's weekly allowance less its reserve, plus the overflow cap. The sprint SHALL also state its capacity in points, the budget divided by the price of a point, and the points planned. The plan SHALL never wait on the author. When the author changes focus during a sprint, the plan SHALL be redone from the new order.

#### Scenario: Plan fills the budget
- GIVEN a sprint budget of 100 units and backlog estimates of 40, 40 and 30
- WHEN the sprint is planned
- THEN the first two issues are planned and the third is not

#### Scenario: How far the budget goes
- GIVEN a sprint budget of 100 units and a price of 4 units per point
- WHEN the sprint is read
- THEN its capacity is 25 points

#### Scenario: Audits share the budget
- GIVEN a sprint budget of 100 units, backlog estimates of 40 and 40, and a due audit estimated at 30
- WHEN the sprint is planned
- THEN the plan's estimates together do not exceed 100 units
- AND the audit's planned tokens count against the budget like an issue's

#### Scenario: Shared account adds to the budget
- GIVEN the factory account's usable allowance is 80 units
- AND the account main's 65 percent share less its reserve is 52 units
- AND no overflow cap
- WHEN the sprint budget is read
- THEN it is 132 units

#### Scenario: Focus changes mid-sprint
- GIVEN a sprint in progress
- WHEN the author sets a new focus
- THEN the plan is redone from the new backlog order

### Requirement: The sprint is reviewed

At the end of each sprint the author SHALL receive one push notification and a review saying what was delivered and is available; the points planned and the points delivered; for each size, how actual tokens compared with the estimate; the issues whose actual tokens were more than twice their estimate, as sizes misjudged; what was spent on each Claude account, each model and on overflow; which audits ran with what they found; and what carried over. The review SHALL keep the points delivered sprint by sprint, so the factory's velocity can be read over time, and the next sprint SHALL be priced from the updated history.

#### Scenario: Sprint ends
- GIVEN a sprint that delivered two milestones and carried over three issues
- WHEN it ends
- THEN exactly one push notification arrives
- AND the review lists the delivered work, the spending per account, per model and on overflow, and the three carried-over issues

#### Scenario: Points planned against delivered
- GIVEN a sprint that planned 40 points and closed issues worth 31
- WHEN it ends
- THEN the review shows 40 points planned and 31 delivered
- AND 31 is added to the velocity history

#### Scenario: Size misjudged
- GIVEN a 2-point issue estimated at 8 units that used 20
- WHEN the sprint ends
- THEN the review lists it as a size misjudged

#### Scenario: Audits in the review
- GIVEN a sprint in which a security audit of reli filed two findings
- WHEN it ends
- THEN the review lists reli's security audit with its two findings

### Requirement: The factory reviews itself

At the end of each sprint the factory SHALL review its own misses, such as sizes misjudged, retries and rework, and record its findings as notes. A finding that needs a standing rule SHALL become a question to the author; any other finding SHALL become work in the backlog.

#### Scenario: Repeated estimate miss
- GIVEN implementation issues in one capability used more than twice their estimates all sprint
- WHEN the sprint is reviewed
- THEN a finding note records the miss
