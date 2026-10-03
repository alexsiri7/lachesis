# Work Notes

## Purpose

Agents do not message each other directly. They leave notes on the work itself, so every handoff, finding and decision stays with what it is about, and the next agent to pick that work up starts from what the last one knew. Notes are the only messages between agents, which keeps every exchange on the record and costs no tokens in conversation.

## Requirements

### Requirement: Notes attach to work

The system SHALL let any caller add a note to an issue or a milestone, with its author's role, its type (handoff, finding, progress, decision or report) and its text. A note SHALL be attached to exactly one issue or milestone.

#### Scenario: Handoff note
- GIVEN issue 160 is in progress
- WHEN the implementer adds a handoff note to it
- THEN the note is stored on issue 160 with role implementer, type handoff, its text and time

#### Scenario: Nothing to attach to
- GIVEN any repository
- WHEN a note is added naming neither an issue nor a milestone
- THEN it is refused

### Requirement: Notes are append-only

Notes SHALL NOT be edited or deleted. A correction SHALL be a new note.

#### Scenario: Editing refused
- GIVEN a note on issue 160
- WHEN a caller tries to change its text
- THEN the change is refused and the note is unchanged

### Requirement: Notes travel with their work

The system SHALL return the notes on an issue or milestone oldest first. When an issue is offered as the next issue to work on, its notes SHALL be returned with it.

#### Scenario: Picking up after a crash
- GIVEN issue 160 has a handoff note from an earlier run
- WHEN issue 160 is offered as the next issue
- THEN its notes are returned with it, oldest first
