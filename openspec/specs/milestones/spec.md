# Milestones

## Purpose

A milestone is the slice of a project the author wants delivered next. It groups spec changes, and it is done only when everything in them is live in production. The author creates milestones and reads their reports; the factory owns them. An unattended milestone owner reviews each active milestone on a schedule, creates the work its spec changes need, checks that nothing has been dropped, asks the author only when it meets a decision, and reports when the milestone is done.

## Requirements

### Requirement: A milestone groups spec changes

The system SHALL let the author create a milestone in a registered repository with a title and a goal, start it, drop it, and add or remove spec changes until it is done. A spec change SHALL belong to at most one milestone.

#### Scenario: Creating a milestone
- GIVEN spec changes 170 and 172
- WHEN a milestone is created with both
- THEN reading it returns its title, goal and both spec changes

#### Scenario: Already in another milestone
- GIVEN spec change 170 belongs to milestone A
- WHEN it is added to milestone B
- THEN the addition is refused and names milestone A

### Requirement: Milestone state is read from the work

A milestone SHALL be planned until the author starts it; active once started; verifying when every spec change in it has merged and every issue implementing them has closed; and done when every one of those issues closed with a merged pull request and the repository's production deployment includes those merges. A dropped milestone SHALL stay dropped. The system SHALL NOT store any state that can be read from the work.

#### Scenario: Merged but not yet live
- GIVEN an active milestone whose issues have all merged
- AND the production deployment does not yet include the last merge
- WHEN the milestone is read
- THEN it is verifying

#### Scenario: Live
- GIVEN a verifying milestone
- WHEN the production deployment includes its last merge
- THEN reading it reports done

### Requirement: Milestone review finds what is missing

The system SHALL review an active milestone and return its gaps: requirements added or changed by its spec changes that no issue names, issues closed without a merged pull request, merges not yet in production, and open questions blocking its work.

#### Scenario: Requirement without work
- GIVEN spec change 170 in an active milestone adds a requirement no issue names
- WHEN the milestone is reviewed
- THEN the gaps name spec change 170 and that requirement

#### Scenario: Work dropped
- GIVEN an issue implementing spec change 170 closed without a merged pull request
- WHEN the milestone is reviewed
- THEN the gaps name that issue

### Requirement: The factory owns the milestone

The agent guidance for unattended agents SHALL describe the milestone owner: on each scheduled run it reviews every active milestone, creates issues for requirements without work, opens spec-gap issues for work closed without merging, asks a question for any gap it cannot close without the author, and adds a progress note to the milestone. The milestone owner SHALL NOT edit the spec.

#### Scenario: Owner guidance
- GIVEN an unattended agent
- WHEN it reads the agent guidance
- THEN the milestone owner workflow is included

### Requirement: Done is reported once

When a milestone becomes done, the author SHALL receive one push notification through landing-notifications saying the milestone is done and available, and a report note SHALL be added to the milestone summarising what was delivered.

#### Scenario: Milestone goes live
- GIVEN a verifying milestone
- WHEN its last merge reaches production
- THEN exactly one push notification names the milestone as done and available
- AND the milestone carries a report note
