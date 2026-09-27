# Agent Guidance

## Purpose

Agents using Lachesis learn the intended workflow from Lachesis itself, so the guidance travels with the server and changes when it changes.

## Requirements

### Requirement: The workflow is available on request

The system SHALL return the standard workflow on request, and SHALL accept an optional repository so the answer can say which workflow applies there. For a repository that uses specs the guidance SHALL say: read the relevant capabilities, propose a spec change carrying the why, then create issues that name the spec change, capability and requirement, with no linking step; and file work that brings code in line with the existing spec as spec gaps. For a repository without specs it SHALL give the requirement-file workflow: create a requirement, create its issues, link them, and that an unlinked requirement is a draft. Both SHALL cover that new issues are ready and last in the backlog, and how to park, order, interrupt and ask for the next issue. Both SHALL also cover the question loop: an agent that cannot tell what the spec or requirement intends asks the author through the system and moves on rather than guessing; and an agent starting a session with the author reads the dashboard first and raises the questions waiting on them.

#### Scenario: Session start in a spec repository
- GIVEN a repository that uses specs
- WHEN guidance is requested for it
- THEN it describes the spec-change workflow and not the requirement-file workflow

#### Scenario: Session start without a repository
- GIVEN no repository is named
- WHEN guidance is requested
- THEN both workflows are described with how to tell which applies

#### Scenario: Stuck on intent
- GIVEN any repository
- WHEN guidance is requested
- THEN it says to ask the author through the system rather than guess, and to move on to other work

### Requirement: Guidance matches behaviour

The guidance SHALL describe the system as it currently behaves. Any change to behaviour covered by the guidance SHALL update the guidance in the same change.

#### Scenario: Behaviour changes
- GIVEN a change alters how issues become eligible for pickup
- WHEN that change lands
- THEN the guidance describes the new behaviour
