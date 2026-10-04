# Questions

## Purpose

Questions are decisions only the author can make. They block work, they outlive the issue that raised them, and their answers become part of how projects are run: a standing rule, a spec change, a new project, or simply an answer. Questions are kept as records of their own, not only as issue comments, so the author has one queue to answer, agents never ask the same thing twice, and the answers form a decision log.

## Requirements

### Requirement: A question is a record

The system SHALL store each question as a record of its own holding: the question, the context needed to answer it, optional suggested options, its kind, who asked it, what it is about (a repository, and optionally a capability, spec change, milestone or issue) and the issues it blocks. Kinds SHALL be spec contradiction, large work, maturity, improvement, budget and clarification. Questions SHALL survive server restarts and SHALL NOT be lost when an issue they mention closes.

#### Scenario: Asking with options
- GIVEN the worker cannot tell whether a change contradicts the backlog capability
- WHEN it asks a question of kind spec contradiction with two options, blocking issue 160
- THEN the question is stored with its text, context, both options, kind, asker and issue 160 as blocked

#### Scenario: Issue closed before the answer
- GIVEN an open question that mentions issue 160
- WHEN issue 160 is closed
- THEN the question is still open and readable

### Requirement: Budget questions never block work

A question of kind budget asks the author to raise the spending cap for models outside the Claude allowance, and SHALL state what the extra spend would deliver. It SHALL NOT block any issue: while it is open, work continues within the existing cap.

#### Scenario: Asking for more budget
- GIVEN the factory has reached its spending cap
- WHEN it asks a budget question naming issue 160 as work the extra spend would deliver
- THEN the question is stored with what the spend would deliver
- AND issue 160 is not labelled needs-author and stays eligible for pickup

### Requirement: Asking blocks the work it names

Asking a question of any kind other than budget SHALL, in the same operation, mark every issue it blocks as waiting on the author as described in issues. An issue blocked by more than one open question SHALL stay waiting until all of them are answered.

#### Scenario: One question, two issues
- GIVEN ready issues 160 and 161
- WHEN a question is asked blocking both
- THEN both are labelled needs-author and skipped for pickup

#### Scenario: Two questions, one issue
- GIVEN issue 160 is blocked by two open questions
- WHEN one of them is answered
- THEN issue 160 is still waiting on the author

### Requirement: The author is told at once

Every new question SHALL reach the author as a push notification through the channel described in landing-notifications, naming the repository, the question and its kind.

#### Scenario: New question
- GIVEN the author's notification channel is configured
- WHEN a question is asked
- THEN a push notification arrives naming its repository, question and kind

### Requirement: One queue of open questions

The system SHALL list open questions across every registered repository, oldest first, each with its kind, what it is about, what it blocks and how long it has waited. The author's assistant and agents SHALL read the same list.

#### Scenario: Reading the queue
- GIVEN open questions in two repositories
- WHEN open questions are listed
- THEN both appear oldest first, each with its kind, subject, blocked issues and waiting time

### Requirement: Answering records the outcome

Answering a question SHALL record the answer, who answered and when, and its outcome: none, standing rule, spec change or new project. In the same operation it SHALL post the answer on every issue the question blocks and release each of those issues that no other open question still blocks.

#### Scenario: Answer that becomes a rule
- GIVEN an open question blocking issue 160
- WHEN it is answered with outcome standing rule
- THEN the question records the answer, answerer, time and outcome
- AND issue 160 carries the answer as its latest comment and is eligible for pickup again

### Requirement: Questions are not asked twice

The system SHALL let any caller search answered questions by repository and text, returning each match with its answer and outcome. The agent guidance SHALL tell agents to search answered questions before asking, and to act on a matching answer instead of asking again.

#### Scenario: Already answered
- GIVEN an answered question about whether the backlog may use priority labels
- WHEN answered questions in that repository are searched for priority labels
- THEN the question is returned with its answer and outcome

#### Scenario: Guidance says search first
- GIVEN any agent
- WHEN it reads the agent guidance
- THEN the guidance tells it to search answered questions before asking
