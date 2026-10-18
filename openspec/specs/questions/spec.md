# Questions

## Purpose

Questions are what only the author can settle: decisions about what a project should be, and tasks only the author can do or provide, such as a secret, an account, a DNS record or access to a service. They block work, they outlive the issue that raised them, and their answers become part of how projects are run: a standing rule, a spec change, a new project, or simply an answer. Questions are kept as records of their own, not only as issue comments, so the author has one queue to answer, agents never ask the same thing twice, and the answers form a decision log. Work is split so that the author's part is as small as it can be and everything else stays with the agents.

## Requirements

### Requirement: A question is a record

The system SHALL store each question as a record of its own holding: the question, the context needed to answer it, optional suggested options, its kind, who asked it, what it is about (a repository, and optionally a capability, spec change, milestone or issue) and the issues it blocks. Kinds SHALL be spec contradiction, large work, maturity, improvement, budget, clarification and author task. Questions SHALL survive server restarts and SHALL NOT be lost when an issue they mention closes.

#### Scenario: Asking with options
- GIVEN the worker cannot tell whether a change contradicts the backlog capability
- WHEN it asks a question of kind spec contradiction with two options, blocking issue 160
- THEN the question is stored with its text, context, both options, kind, asker and issue 160 as blocked

#### Scenario: Issue closed before the answer
- GIVEN an open question that mentions issue 160
- WHEN issue 160 is closed
- THEN the question is still open and readable

### Requirement: Author tasks ask for something only the author can do

A question of kind author task SHALL ask the author to do or provide one concrete thing that no agent can: a secret or API key, an account or sign-up, a DNS record, a payment, or access to a service. It SHALL state exactly what to do, where, and how the agent will know it is done (for example, which variable must be set on which service).

#### Scenario: Asking for a key
- GIVEN the worker needs an API key set on a Railway service before issue 160 can be finished
- WHEN it asks an author task naming the variable, the service and the provider to get the key from, blocking issue 160
- THEN the question is stored with kind author task and those details
- AND issue 160 is labelled needs-author

### Requirement: An author task waits for the author

A question of kind author task SHALL NOT carry a default. The author answers it by saying it is done, or by handing over what was asked for when that is safe to record; the answer releases the issues it blocks as any answer does.

#### Scenario: No default for an author task
- GIVEN an author task asked a week ago
- WHEN a default is offered for it
- THEN the default is refused and the task keeps waiting for the author

### Requirement: Work that needs the author is split

When an issue is triaged or sized, the agent SHALL separate the steps only the author can take from the rest. Each such step SHALL become an author task blocking only the issues that cannot finish without it, and every step an agent can do SHALL stay in agent issues that remain ready. An issue SHALL NOT be left holding author-only steps without a matching author task.

#### Scenario: Provisioning split
- GIVEN an issue to deploy a service that needs a hosting project created, an API key from the author and a DNS record in the author's DNS provider
- WHEN the issue is triaged
- THEN the agent work (configuration as code, documentation, health checks) stays in ready issues
- AND the API key and the DNS record are each an author task blocking only the issue that verifies the live service

### Requirement: A run never parks silently on an author-only step

A run that cannot continue without something only the author can do or provide SHALL ask an author task naming exactly the missing step before it ends, unless an open question already covers it. It SHALL NOT end with the issue released and no question recorded.

#### Scenario: Missing access found mid-run
- GIVEN a run on issue 160 discovers it has no access to the DNS provider
- WHEN the run ends
- THEN an author task asking for the DNS record is open and blocks issue 160

### Requirement: Small questions settle by default while the author is away

A question of kind clarification or improvement MAY carry a default answer and a wait, three days unless the asker gives another. When the wait passes with no answer, the system SHALL record the default as the answer with the outcome `defaulted`, release the issues it blocked as an answer would, and note on each that the default was taken. The author MAY later answer a defaulted question, which records the new answer and its outcome as usual.

#### Scenario: Away for a week
- GIVEN a clarification question with a default, blocking issue 160, asked four days ago and unanswered
- WHEN the open questions are read
- THEN the question is answered with its default and the outcome defaulted
- AND issue 160 is released with a note saying the default was taken

### Requirement: Other questions wait for the author

Questions of every kind other than clarification and improvement, which decide what a project should be or ask the author to act, SHALL NOT carry a default and SHALL wait for the author.

#### Scenario: Spec decision waits
- GIVEN a spec contradiction question asked a month ago
- WHEN a default is offered for it
- THEN the default is refused and the question keeps waiting for the author

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

The system SHALL list open questions across every registered repository, oldest first, each with its kind, what it is about, what it blocks and how long it has waited. Author tasks SHALL be listable on their own, so the author can see everything they have to do in one place. The author's assistant and agents SHALL read the same list.

#### Scenario: Reading the queue
- GIVEN open questions in two repositories
- WHEN open questions are listed
- THEN both appear oldest first, each with its kind, subject, blocked issues and waiting time

#### Scenario: Only my tasks
- GIVEN an open clarification question and an open author task
- WHEN open questions are listed filtered to author tasks
- THEN only the author task is returned

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
