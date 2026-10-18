# Audits

## Purpose

Acceptance tests check only what the scenarios say. Audits look for everything else: behaviour the spec does not describe, tests that do not really check their scenario, requirements the code does not meet, spec wording that could mean two things, weak architecture, security problems and code that falls short of clean code standards. Audits report and ask; they never fix. Their questions go to the author, because only the author decides what the project should be.

Audits are factory work like any other: they are planned into the sprint, paid for from its budget and recorded run by run, so the factory can tell when each audit last ran on each project and what it found, and spend audits where the code is actually changing.

## Requirements

### Requirement: Every audit run is recorded

The system SHALL record every audit run with its repository, its audit kind (architecture, security, clean code, tests, requirements or north star), its capability where the audit has one, the model that ran it, when it ran, its outcome (clean, findings or failed), the issues it filed and the tokens it used. It SHALL return, for each registered repository and audit kind, the last run with its time and outcome, and say "never" for an audit that has never run there.

#### Scenario: Run recorded
- GIVEN a security audit of filmduel that finished and filed issues #40 and #41
- WHEN its run is reported
- THEN the audit history shows filmduel's last security audit at that time with outcome findings and issues #40 and #41

#### Scenario: Failed run recorded
- GIVEN a tests audit of reli that stopped before finishing
- WHEN its run is reported as failed
- THEN the audit history shows reli's last tests audit as failed
- AND the audit stays due

#### Scenario: Never audited
- GIVEN a registered repository no security audit has ever run on
- WHEN the audit history is read
- THEN its security audit shows never

### Requirement: Audits fall due when the code or the auditor changes

An audit kind SHALL be due for a repository when it has never run there, when its last run there failed, when pull requests have merged into the repository's default branch since its last successful run there, or when the model the factory would now use for that audit kind differs from the model of that last successful run, since a different model may find what the previous one missed. Otherwise, and apart from the 60-day security audit, an audit SHALL NOT be due.

#### Scenario: Quiet project skipped
- GIVEN kith's last tests audit finished after its most recent merge, on the model still used for tests audits
- WHEN due audits are computed
- THEN no tests audit of kith is due

#### Scenario: New auditor model
- GIVEN every repository's last architecture audit ran on one model and no repository has changed since
- WHEN the model used for architecture audits changes
- THEN an architecture audit is due for every repository

### Requirement: Due audits are ordered by merges

Due audits SHALL be ordered by the number of pull requests merged since their last run, most first, then by the oldest last run.

#### Scenario: Busiest project first
- GIVEN twelve pull requests merged into reli and two into filmduel since each one's last tests audit
- WHEN due audits are computed
- THEN reli's tests audit comes before filmduel's

### Requirement: A security audit falls due every 60 days

A security audit SHALL also be due once 60 days have passed since its last successful run, changes or not, because dependencies age while the code stands still.

#### Scenario: Security audit of a quiet project
- GIVEN kith has had no merges since its last security audit, which finished 61 days ago
- WHEN due audits are computed
- THEN a security audit of kith is due

### Requirement: Audits are planned into the sprint

Due audits SHALL be planned into the sprint alongside issues and drawn by the factory in the same way. Each audit SHALL carry an estimate in tokens made from the measured usage of past runs of the same kind, marked low confidence until there are five such runs, and its tokens SHALL count against the sprint budget. The system SHALL NOT start an audit in a repository while an earlier audit there is still running or its audit issue is still open.

#### Scenario: Audit in the plan
- GIVEN a due security audit of reli whose past security runs used about 30 units
- AND room in the sprint budget
- WHEN the sprint is planned
- THEN the plan includes reli's security audit with an estimate of about 30 units

#### Scenario: Previous audit unfinished
- GIVEN an open audit issue in a repository
- WHEN the factory draws its next work
- THEN it is not offered a new audit of that repository

### Requirement: Spec audits go one capability at a time

In a repository that uses specs, a tests or requirements audit SHALL cover one capability: the one audited least recently by that kind, a capability never audited coming first. Its audit issue SHALL be labelled `audit` and name the capability.

#### Scenario: Least recently audited capability
- GIVEN a repository whose backlog capability has never had a tests audit and whose other capabilities have
- WHEN its tests audit starts
- THEN its audit issue names backlog

### Requirement: An audit covers spec, tests and code

A tests or requirements audit issue in a repository that uses specs SHALL direct its auditor to check, for its capability: behaviour in the code that no scenario describes; scenarios whose acceptance test does not genuinely check them; requirements the code does not meet; and wording in the spec open to more than one reading.

#### Scenario: Audit issue content
- GIVEN an audit is opened for the backlog capability
- WHEN the issue is read
- THEN it lists the four checks for backlog

### Requirement: A clean code audit applies Robert C. Martin's standards

A clean code audit issue SHALL direct its auditor to check the code against Robert C. Martin's clean code standards: names that reveal intent; small functions that do one thing at one level of abstraction; few arguments and no flag arguments; no side effects hidden behind a name; no duplication; and comments that explain why rather than restate what.

#### Scenario: Clean code audit content
- GIVEN a clean code audit is started for a repository
- WHEN its audit issue is read
- THEN it lists those standards
- AND it asks for each finding to name the standard broken and where

### Requirement: A clean code audit checks design and tests

A clean code audit issue SHALL also direct its auditor to check the code against these of Robert C. Martin's clean code standards: classes and modules with a single responsibility; dependencies that point inward, so policy never depends on detail; and tests that are fast, independent, repeatable, self-validating and as clean as the code.

#### Scenario: Design and test standards listed
- GIVEN a clean code audit is started for a repository
- WHEN its audit issue is read
- THEN it lists single-responsibility classes and modules, dependencies that point inward, and tests that are fast, independent, repeatable, self-validating and as clean as the code

### Requirement: Clean code findings name the standard broken

Each finding of a clean code audit SHALL name the standard it breaks and where.

#### Scenario: Finding names its standard
- GIVEN a clean code audit is started for a repository
- WHEN its audit issue is read
- THEN it asks for each finding to name the standard it breaks and where

### Requirement: A north-star audit holds the specs to the north star

In a repository with a north star (`docs/north-star.md`), a north-star audit issue SHALL direct its auditor to take each story in turn and find what in the specs, the backlog or the factory's behaviour stands between the system and that story. Each gap SHALL be filed as a spec-gap issue when the specs already promise the behaviour, or as a question to the author proposing the spec change that would close it when they do not.

#### Scenario: North-star audit content
- GIVEN a north-star audit is started for a repository with a north star
- WHEN its audit issue is read
- THEN it lists the north star's stories
- AND it says how to file a gap the specs already cover and one they do not

### Requirement: Findings are filed, not fixed

An audit SHALL NOT change code, tests or specs. Code that fails a requirement SHALL be filed as a spec-gap issue. A test that does not genuinely check its scenario SHALL be filed as a spec-gap issue labelled `acceptance-tests`. The audit issue SHALL close once every finding is filed, listing them.

#### Scenario: Weak test found
- GIVEN an audit finds a test that passes without checking its scenario's outcome
- WHEN the finding is filed
- THEN it becomes a spec-gap issue labelled acceptance-tests for that capability

### Requirement: Decisions go to the author

Behaviour the spec does not describe and wording open to more than one reading SHALL each become their own spec-gap issue, marked as waiting on the author with a question framed as a decision: for undescribed behaviour, whether to keep it and add it to the spec or remove it; for ambiguous wording, which reading is intended.

#### Scenario: Undescribed behaviour
- GIVEN an audit finds the code retries failed writes three times and no scenario mentions retries
- WHEN the finding is filed
- THEN a spec-gap issue waits on the author asking whether to keep the retries and specify them, or remove them

### Requirement: Audit history is visible

Progress reporting SHALL show, for each registered repository and audit kind, when it last ran, its outcome and its open findings, and for each capability of a repository that uses specs, when it was last audited.

#### Scenario: History on the dashboard
- GIVEN filmduel's last security audit ran three days ago and left one finding open
- WHEN the dashboard is requested
- THEN filmduel shows its security audit three days ago with one open finding

#### Scenario: Capability never audited
- GIVEN a capability that has never been audited
- WHEN the dashboard is requested
- THEN the capability shows it has never been audited
