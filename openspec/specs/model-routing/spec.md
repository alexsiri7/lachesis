# Model Routing

## Purpose

Not every kind of work, nor every size of it, needs the most capable model. The factory decides which models are good enough for which kinds of work, from evidence it gathers itself, and keeps revising that as models change. Trust is earned on low-risk work first and lost on failure. When Claude is chosen, the factory also decides which of its accounts pays for the run.

## Requirements

### Requirement: A scorecard per model and kind of work

The system SHALL keep, for each model, kind of work and issue size, the number of runs, how many merged without rework, retries, later audit findings against that work, tokens, cost, and tokens and cost per point delivered. Kinds of work SHALL include triage, summarising, audit, tests, implementation and spec work. Models SHALL include each Claude model on the allowance (such as Opus, Sonnet and Haiku) and each model reached through overflow. The scorecard SHALL be readable.

#### Scenario: Reading the scorecard
- GIVEN runs by two models on triage and implementation
- WHEN the scorecard is read
- THEN each model, kind and size shows runs, merged without rework, retries, audit findings, tokens, cost and cost per point

### Requirement: Routing picks the cheapest trusted model

For each run the system SHALL choose the model with the lowest cost per point among those trusted for its kind of work at its size and within the fuel available. The most capable Claude model SHALL be trusted for every kind and size by default; every other model, smaller Claude models included, SHALL earn trust. Implementation and spec work SHALL use the most capable Claude model until another model earns trust for that kind and size.

#### Scenario: Cheap model for triage
- GIVEN a model trusted for triage that costs less than Claude
- WHEN a triage run is routed
- THEN that model is chosen

#### Scenario: Smaller model for small work
- GIVEN Sonnet trusted for implementation up to 3 points, at a lower cost per point than Opus
- WHEN a 2-point implementation run is routed
- THEN Sonnet is chosen
- AND a 5-point implementation run is routed to Opus

### Requirement: Routing names the account

When a run is routed to Claude, the system SHALL name the account to use: among the accounts that are not paused, are within their pace and have allowance the run may use as described in fuel, the one with the most such allowance left, the primary account winning a tie. When no account qualifies, the run SHALL be routed as fuel describes for overflow.

#### Scenario: Two accounts available
- GIVEN the factory account with 10 percent of its allowance usable
- AND the account main with 30 percent usable and not paused
- WHEN an implementation run is routed
- THEN Claude on the account main is chosen

#### Scenario: Shared account paused
- GIVEN the account main is paused
- AND the factory account has allowance usable
- WHEN an implementation run is routed
- THEN Claude on the factory account is chosen

### Requirement: Trust is earned and lost

A model SHALL become trusted for a kind of work at a size after five consecutive runs of that kind at that size or larger that merged without rework and drew no audit finding; trust at a size covers every smaller size.

#### Scenario: Earning trust
- GIVEN a model with four clean triage runs in a row
- WHEN its fifth triage run merges cleanly with no audit finding
- THEN it is trusted for triage

### Requirement: Trust is lost on repeated failure

A model SHALL lose trust at a size after two failures in its last five runs of that kind at that size, where a failure is rework, a retry or an audit finding.

#### Scenario: Losing trust
- GIVEN a model trusted for tests
- WHEN two of its last five test runs needed rework
- THEN it is no longer trusted for tests

### Requirement: New models trial from the lowest risk up

New models SHALL trial on triage and summarising before any other kind, SHALL trial on a kind only once trusted on a lower-risk one, and SHALL trial on 1-point work of a kind before larger sizes, stepping up one size at a time.

#### Scenario: Stepping up a size
- GIVEN a model trusted for implementation up to 2 points
- WHEN it is next trialled on implementation
- THEN the trial is a 3-point issue

### Requirement: Trials are budgeted

Each sprint SHALL set aside a trial share of its budget, 10 percent unless the author sets another, for runs by models not yet trusted for the kind and size they run. Trials SHALL be planned like any other work and SHALL NOT exceed the trial share. A trial that fails SHALL be redone by a trusted model within the same sprint.

#### Scenario: Trial share respected
- GIVEN a sprint budget of 100 units with the default trial share
- WHEN the sprint is planned
- THEN no more than 10 units go to trial runs

#### Scenario: Failed trial redone
- GIVEN a trial implementation run by Haiku that needed rework
- WHEN the rework is routed
- THEN a model trusted for that kind and size does it
