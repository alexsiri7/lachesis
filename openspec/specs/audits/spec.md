# Audits

## Purpose

Acceptance tests check only what the scenarios say. Audits look for everything else: behaviour the spec does not describe, tests that do not really check their scenario, requirements the code does not meet, and spec wording that could mean two things. Audits report and ask; they never fix. Their questions go to the author, because only the author decides what the project should be.

## Requirements

### Requirement: Audits are scheduled one capability at a time

For each repository that uses specs, the system SHALL open an audit issue for the capability audited least recently, at a cadence set per repository and defaulting to weekly. It SHALL NOT open a new audit in a repository while an earlier one there is still open. Audit issues SHALL be labelled `audit` and name the capability.

#### Scenario: Weekly rotation
- GIVEN a repository whose backlog capability has never been audited and whose other capabilities have
- WHEN the next audit is due
- THEN an audit issue is opened for backlog

#### Scenario: Previous audit unfinished
- GIVEN an open audit issue in a repository
- WHEN the next audit is due
- THEN no new audit issue is opened there

### Requirement: An audit covers spec, tests and code

An audit issue SHALL direct its auditor to check, for its capability: behaviour in the code that no scenario describes; scenarios whose acceptance test does not genuinely check them; requirements the code does not meet; and wording in the spec open to more than one reading.

#### Scenario: Audit issue content
- GIVEN an audit is opened for the backlog capability
- WHEN the issue is read
- THEN it lists the four checks for backlog

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

Progress reporting SHALL show, for each capability of a repository that uses specs, when it was last audited and its open audit findings.

#### Scenario: Never audited
- GIVEN a capability that has never been audited
- WHEN the dashboard is requested
- THEN the capability shows it has never been audited
