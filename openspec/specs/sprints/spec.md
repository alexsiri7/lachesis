# Sprints

## Purpose

The primary Claude account's allowance resets weekly, so the factory works in weekly sprints aligned with it. It improves its own way of working every sprint through small measured experiments, sizes work in Fibonacci points, learns from every finished issue how many tokens a point costs, plans as many points as the budget buys, measures everything and reviews the sprint once a week. The sprint is the factory's own governor: a regular point to stop, measure and re-plan without the author acting as the brake. Carryover is information, not failure.

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

At the start of each sprint the factory SHALL plan it: take the computed backlog in order, together with the due audits, and include work until its estimates fill the sprint budget, which is the factory share of every registered account's weekly allowance less its reserve, unless that reserve is released in the last 24 hours before its account's reset, plus the overflow cap. The plan SHALL never wait on the author.

#### Scenario: Plan fills the budget
- GIVEN a sprint budget of 100 units and backlog estimates of 40, 40 and 30
- WHEN the sprint is planned
- THEN the first two issues are planned and the third is not

#### Scenario: Audits share the budget
- GIVEN a sprint budget of 100 units, backlog estimates of 40 and 40, and a due audit estimated at 30
- WHEN the sprint is planned
- THEN the plan's estimates together do not exceed 100 units
- AND the audit's planned tokens count against the budget like an issue's

#### Scenario: Shared account adds to the budget
- GIVEN the factory account's usable allowance is 90 units
- AND the account main's 65 percent share less its reserve is 58.5 units
- AND no overflow cap
- WHEN the sprint budget is read
- THEN it is 148.5 units

### Requirement: The sprint states its capacity in points

The sprint SHALL state its capacity in points, the budget divided by the price of a point, and the points planned.

#### Scenario: How far the budget goes
- GIVEN a sprint budget of 100 units and a price of 4 units per point
- WHEN the sprint is read
- THEN its capacity is 25 points

### Requirement: A focus change redoes the plan

When the author changes focus during a sprint, the plan SHALL be redone from the new order.

#### Scenario: Focus changes mid-sprint
- GIVEN a sprint in progress
- WHEN the author sets a new focus
- THEN the plan is redone from the new backlog order

### Requirement: The sprint is reviewed

At the end of each sprint the author SHALL receive one push notification and a review saying what was delivered and is available; the points planned and the points delivered; what was spent on each Claude account, each model and on overflow; which audits ran with what they found; the improvement experiments with their measures and results; and what carried over.

#### Scenario: Sprint ends
- GIVEN a sprint that delivered two milestones and carried over three issues
- WHEN it ends
- THEN exactly one push notification arrives
- AND the review lists the delivered work, the spending per account, per model and on overflow, and the three carried-over issues

#### Scenario: Audits in the review
- GIVEN a sprint in which a security audit of reli filed two findings
- WHEN it ends
- THEN the review lists reli's security audit with its two findings

### Requirement: The review compares tokens with estimates

The sprint review SHALL also say, for each size, how actual tokens compared with the estimate, and list the issues whose actual tokens were more than twice their estimate, as sizes misjudged.

#### Scenario: Size misjudged
- GIVEN a 2-point issue estimated at 8 units that used 20
- WHEN the sprint ends
- THEN the review lists it as a size misjudged

### Requirement: Velocity is kept sprint by sprint

The review SHALL keep the points delivered sprint by sprint, so the factory's velocity can be read over time, and the next sprint SHALL be priced from the updated history.

#### Scenario: Points planned against delivered
- GIVEN a sprint that planned 40 points and closed issues worth 31
- WHEN it ends
- THEN the review shows 40 points planned and 31 delivered
- AND 31 is added to the velocity history

### Requirement: The factory reviews itself

At the end of each sprint the factory SHALL review its own misses, such as sizes misjudged, retries and rework, and record its findings as notes. A finding that needs a standing rule about what a project should be SHALL become a question to the author; any other finding SHALL become work in the backlog.

#### Scenario: Repeated estimate miss
- GIVEN implementation issues in one capability used more than twice their estimates all sprint
- WHEN the sprint is reviewed
- THEN a finding note records the miss

### Requirement: The factory improves itself every sprint

Each sprint review SHALL choose one or two improvements to how the factory works (its guidance, sizing, routing, scripts or checks, never what a project should be) from that sprint's misses, audit findings and north-star gaps. Each SHALL be recorded as an experiment: the change, the measure it should move (such as rework rate, tokens per point, time stuck or questions per issue), its value now and the target.

#### Scenario: Experiment chosen
- GIVEN a sprint in which four issues needed rework after review
- WHEN the sprint is reviewed
- THEN the review records an experiment naming a change, the rework rate as its measure, its current value and a target
- AND the work it needs is planned first in the next sprint

### Requirement: Experiments are judged at the next review

The next review SHALL judge each experiment against its measure: one that met its target SHALL be kept and made standard, by changing the guidance or proposing a spec change; one that did not SHALL be reverted or given one more sprint with a stated reason. The review SHALL list every open experiment with its measure and result.

#### Scenario: Experiment kept
- GIVEN an experiment whose target was a rework rate under 10 percent
- AND the sprint since it started had a rework rate of 6 percent
- WHEN the sprint is reviewed
- THEN the experiment is marked kept and its change made standard

#### Scenario: Experiment reverted
- GIVEN an experiment that has missed its target for two sprints
- WHEN the sprint is reviewed
- THEN the experiment is marked reverted and its change undone

#### Scenario: Open experiments listed
- GIVEN two open experiments
- WHEN the sprint is reviewed
- THEN the review lists both with their measures and results

### Requirement: Experiment work is planned first within the improvement share

The work an experiment needs SHALL be planned in the next sprint ahead of the ordinary backlog, within an improvement share of the sprint budget, 10 percent unless the author sets another.

#### Scenario: Improvement share respected
- GIVEN a sprint budget of 100 units with the default improvement share
- WHEN the sprint is planned
- THEN no more than 10 units go to improvement work
- AND that work is planned before the ordinary backlog

### Requirement: Improving the factory does not wait on the author

Changing how the factory works is the factory's to decide and SHALL NOT wait on the author.

#### Scenario: Improvement made without the author
- GIVEN an experiment that met its target
- WHEN the sprint is reviewed
- THEN its change is made standard without waiting on the author

### Requirement: The line stops for a repeated failure

When the same failure happens three times in one sprint, such as the same check failing, the same kind of rework or the same stuck state, the next sprint review SHALL make fixing its cause an experiment, whatever else it would have chosen.

#### Scenario: Same failure three times
- GIVEN three pull requests this sprint got stuck on the same merge conflict pattern
- WHEN the sprint is reviewed
- THEN one of the experiments chosen is to remove that cause
