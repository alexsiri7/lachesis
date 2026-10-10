# Fuel

## Purpose

The factory runs on one or more Claude accounts, each with a weekly allowance, and overflows to other models through Requesty under a spending cap the author sets there. Some accounts are the factory's own; others are shared with the author, who needs part of their allowance for their own use. Requesty is a budget of its own, in dollars, separate from every account. Fuel is how the factory knows what it has spent, what it has left on each account and on Requesty, and what to keep back, so it can pace itself without the author watching.

## Requirements

### Requirement: Accounts are registered

The author SHALL be able to register each Claude account the factory may use, with a name, its factory share (the percentage of its weekly allowance the factory may use, 100 for an account dedicated to the factory), and optionally a five-hour pause threshold. Exactly one account SHALL be primary. The system SHALL return the registered accounts with their settings.

#### Scenario: Registering a shared account
- GIVEN the factory account registered as primary with a 100 percent share
- WHEN the author registers the account main with a 65 percent share and a 60 percent five-hour pause threshold
- THEN both accounts are returned with their shares, main's pause threshold, and the factory account as primary

### Requirement: Every run reports its usage

The worker SHALL report, for every run on an issue, the model used, tokens in and out, money spent where the model is paid per token, the account for a run on a Claude allowance, and the issue and kind of work. The system SHALL store these reports and SHALL NOT accept a report that names no issue, nor a run on a Claude allowance that names no registered account.

#### Scenario: Run on overflow model
- GIVEN a run on issue 160 using a model through Requesty
- WHEN the worker reports its usage
- THEN the report is stored with the model, tokens, cost, issue 160 and its kind of work

#### Scenario: Run on a shared account
- GIVEN a run on issue 161 using Claude on the account main
- WHEN the worker reports its usage
- THEN the report is stored with the model, tokens, issue 161, its kind of work and the account main

### Requirement: The system knows what is left

The system SHALL keep, for each account, the latest allowance status reported for it: the weekly use, when the weekly allowance resets, the five-hour window's use, and any shorter limit in force. A report for one account SHALL NOT change another's, nor the overflow figures. Reading fuel SHALL return every account's status with its reserve and what the factory may still use of it, and the overflow figures.

#### Scenario: Reading fuel
- GIVEN the factory account reported 60 percent of its weekly allowance used
- AND the account main reported 20 percent used
- AND overflow reported 12 dollars of a 40 dollar cap spent
- WHEN fuel is read
- THEN both accounts are returned with their use, reset time and reserve, and the overflow figures

#### Scenario: Reports do not overwrite each other
- GIVEN the factory account reported 60 percent used
- WHEN the account main reports 20 percent used
- THEN the factory account still reads 60 percent used

### Requirement: Overflow is reported on its own

The system SHALL keep the latest overflow report, the money spent through Requesty this sprint and the cap the author set there, separately from every account. Only an overflow report SHALL change the overflow figures. Until overflow is first reported, the cap SHALL be unknown and no work SHALL be routed to overflow.

#### Scenario: Account reports leave overflow alone
- GIVEN overflow reported 12 dollars of a 40 dollar cap spent
- WHEN the account main reports its fuel
- THEN fuel still reads 12 dollars of a 40 dollar cap spent

#### Scenario: Overflow never reported
- GIVEN no overflow report
- AND no account has allowance left outside its reserve
- WHEN a run is routed
- THEN it is not routed to an overflow model

### Requirement: A reserve is kept for emergencies

The system SHALL hold back 10 percent of each account's factory share as a reserve, usable only for security work and production incidents, until 24 hours before that account's weekly allowance resets. From then until the reset, whatever is left of the reserve SHALL be usable for any work, so the factory can use its whole share and no allowance is lost at the reset. Outside that last day, work of any other kind SHALL NOT draw on the reserve. Nothing, emergencies included, SHALL use an account beyond its factory share.

#### Scenario: Only emergencies use the reserve
- GIVEN only the reserve remains on every account
- AND every account's reset is more than 24 hours away
- WHEN the next issue is requested
- THEN only security or incident work is offered

#### Scenario: Unused reserve released before the reset
- GIVEN only the reserve remains on the factory account
- AND its weekly allowance resets in 20 hours
- WHEN the next issue is requested
- THEN work of any kind may be offered on the factory account

#### Scenario: The share is never exceeded
- GIVEN the account main has a 65 percent share and has reported 65 percent used
- WHEN security work is routed
- THEN it is not routed to the account main

### Requirement: A shared account pauses for its owner

While an account's five-hour window is at or above its pause threshold, the system SHALL NOT route work to it, so the author keeps room for their own use. The account SHALL be usable again once a report shows the window below the threshold.

#### Scenario: Five-hour window too full
- GIVEN the account main has a 60 percent pause threshold
- AND its latest report shows its five-hour window 62 percent used
- WHEN a Claude run is routed
- THEN it is not routed to the account main

### Requirement: Work overflows within the cap

When no account has allowance left outside its reserve and its pause, or a shorter limit is in force on every account, the system SHALL route eligible work to overflow models as described in model-routing, while spending stays under the Requesty cap. At the cap, the factory SHALL ask a budget question as described in questions, and SHALL continue with whatever fuel remains or wait for the next reset.

#### Scenario: Reaching the cap
- GIVEN no account has allowance left outside its reserve
- AND overflow spending reaches the Requesty cap
- WHEN more work is ready
- THEN a budget question is asked
- AND no issue is blocked by it

### Requirement: Spending is paced across the week

The system SHALL pace each account across its own weekly allowance, so that no more than its share of what the factory may use of it is used before each remaining day, keeping work moving through the whole week rather than stalling after a burst. An account outside its pace SHALL NOT be routed non-security work.

#### Scenario: Early burst held back
- GIVEN two days into an account's week with 70 percent of its usable allowance outside the reserve already used
- WHEN the next non-security issue is requested
- THEN that account is not used for it until its spending is back within the pace
