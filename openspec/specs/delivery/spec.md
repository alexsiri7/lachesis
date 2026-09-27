# Delivery

## Purpose

No human reviews code. A change reaches the default branch only when machines have checked it against the spec. Acceptance tests turn every scenario into an executable check; they are written first, from the spec, and fail until the implementation makes them pass. The implementation can never change what counts as passing.

## Requirements

### Requirement: Every scenario has an acceptance test

Every scenario in `openspec/specs/` SHALL have exactly one acceptance test, and each acceptance test SHALL declare the capability and scenario it covers. CI SHALL fail when a scenario has no test, when two tests claim the same scenario, or when a test claims a scenario that does not exist.

#### Scenario: Scenario without a test
- GIVEN a branch whose spec contains a scenario no acceptance test claims
- WHEN CI runs
- THEN it fails and names the capability and scenario

#### Scenario: Test for a deleted scenario
- GIVEN an acceptance test claiming a scenario the spec no longer contains
- WHEN CI runs
- THEN it fails and names the test

### Requirement: Acceptance tests use only the public interface

Acceptance tests SHALL exercise the system only through its public interface — MCP tools, HTTP endpoints and web pages — and SHALL NOT import or call internal modules, so that they hold however the code is built.

#### Scenario: Test reaching into internals
- GIVEN an acceptance test that imports an internal module
- WHEN CI runs
- THEN it fails and names the test

### Requirement: Tests come first and start red

Acceptance tests SHALL land with the spec change that introduces or modifies their scenarios, before any implementation. A test for a scenario the code does not yet meet SHALL be marked expected to fail. An expected failure that passes SHALL fail CI, so the marker is removed as soon as the behaviour exists.

#### Scenario: New scenario lands red
- GIVEN a spec change adding a scenario, with its test marked expected to fail
- WHEN CI runs on the spec change
- THEN the expected failure does not fail CI and the spec change can merge

#### Scenario: Behaviour arrives
- GIVEN a test marked expected to fail
- WHEN a change makes it pass without removing the marker
- THEN CI fails and says the marker must be removed

### Requirement: Implementation cannot move the goalposts

A pull request SHALL change acceptance tests only if it is a spec change or implements an issue labelled `acceptance-tests`. Any other pull request SHALL at most remove expected-failure markers from acceptance tests; CI SHALL fail on any other change to them. Implementations MAY add and change their own unit tests freely.

#### Scenario: Implementation edits an assertion
- GIVEN an implementation pull request that changes an assertion in an acceptance test
- WHEN CI runs
- THEN it fails and names the test changed

#### Scenario: Implementation removes a marker
- GIVEN an implementation pull request whose only acceptance-test change removes an expected-failure marker
- WHEN CI runs and the test passes
- THEN the acceptance-test check passes

### Requirement: Machines gate the merge

A pull request SHALL merge only when the spec validates, every scenario has a test, every acceptance and unit test passes apart from expected failures, and the goalpost rule holds. No human approval SHALL be required.

#### Scenario: All green
- GIVEN an implementation pull request that passes every check
- WHEN checks complete
- THEN it merges with no human approval

#### Scenario: One check red
- GIVEN a pull request failing any one of the checks
- WHEN checks complete
- THEN it does not merge

### Requirement: Scenario results are published

Every CI run on the default branch SHALL publish, for each scenario, whether its test passed, failed or is expected to fail, in a form the system can read back.

#### Scenario: After a merge
- GIVEN a merge to the default branch
- WHEN its CI run completes
- THEN the result of every scenario is published for that commit
