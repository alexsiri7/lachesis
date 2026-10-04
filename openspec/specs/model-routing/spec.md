# Model Routing

## Purpose

Not every kind of work needs the most capable model. The factory decides which models are good enough for which kinds of work, from evidence it gathers itself, and keeps revising that as models change. Trust is earned on low-risk work first and lost on failure.

## Requirements

### Requirement: A scorecard per model and kind of work

The system SHALL keep, for each model and kind of work, the number of runs, how many merged without rework, retries, later audit findings against that work, tokens and cost. Kinds of work SHALL include triage, summarising, audit, tests, implementation and spec work. The scorecard SHALL be readable.

#### Scenario: Reading the scorecard
- GIVEN runs by two models on triage and implementation
- WHEN the scorecard is read
- THEN each model and kind shows runs, merged without rework, retries, audit findings, tokens and cost

### Requirement: Routing picks the cheapest trusted model

For each run the system SHALL choose the cheapest model trusted for its kind of work and within the fuel available. Claude SHALL be trusted for every kind by default. Implementation and spec work SHALL use Claude until another model earns trust for that kind.

#### Scenario: Cheap model for triage
- GIVEN a model trusted for triage that costs less than Claude
- WHEN a triage run is routed
- THEN that model is chosen

### Requirement: Trust is earned and lost

A model SHALL become trusted for a kind of work after five consecutive runs of that kind that merged without rework and drew no audit finding. It SHALL lose that trust after two failures in its last five runs of that kind, where a failure is rework, a retry or an audit finding. New models SHALL trial on triage and summarising before any other kind, and SHALL trial on a kind only once trusted on a lower-risk one.

#### Scenario: Earning trust
- GIVEN a model with four clean triage runs in a row
- WHEN its fifth triage run merges cleanly with no audit finding
- THEN it is trusted for triage

#### Scenario: Losing trust
- GIVEN a model trusted for tests
- WHEN two of its last five test runs needed rework
- THEN it is no longer trusted for tests
