# Events

## Purpose

Every change to the factory's records leaves an event, so agents and views can react to change instead of polling everything, and the factory view and cost tracking read from one history.

## Requirements

### Requirement: Changes are recorded

The system SHALL record an event, with its time, kind, subject and repository, whenever a question is asked or answered, a note is added, or a milestone is created, started, dropped, changes state, or gains or loses a spec change.

#### Scenario: Answer recorded
- GIVEN an open question
- WHEN it is answered
- THEN an event of kind question answered names the question and its repository

### Requirement: Events can be followed

The system SHALL return events after a given cursor, oldest first, optionally filtered by repository and kind, together with the cursor for the next read. Events SHALL NOT be changed or deleted.

#### Scenario: Catching up
- GIVEN three events after cursor C
- WHEN events after C are read
- THEN all three are returned oldest first with a new cursor
- AND reading after the new cursor returns none
