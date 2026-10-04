# Fuel

## Purpose

The factory runs on one Claude account with a weekly allowance, and overflows to other models through Requesty under a spending cap the author sets there. Fuel is how the factory knows what it has spent, what it has left and what to keep back, so it can pace itself without the author watching.

## Requirements

### Requirement: Every run reports its usage

The worker SHALL report, for every run on an issue, the model used, tokens in and out, money spent where the model is paid per token, and the issue and kind of work. The system SHALL store these reports and SHALL NOT accept a report that names no issue.

#### Scenario: Run on overflow model
- GIVEN a run on issue 160 using a model through Requesty
- WHEN the worker reports its usage
- THEN the report is stored with the model, tokens, cost, issue 160 and its kind of work

### Requirement: The system knows what is left

The system SHALL keep the latest Claude allowance status reported by the worker, including when the weekly allowance resets and any shorter limit in force, and the overflow money spent this week against the Requesty cap. Reading fuel SHALL return both, with the reserve.

#### Scenario: Reading fuel
- GIVEN the worker reported 60 percent of the weekly allowance used and 12 dollars of a 40 dollar cap spent
- WHEN fuel is read
- THEN both figures are returned with the reset time and the reserve

### Requirement: A reserve is kept for emergencies

The system SHALL hold back 20 percent of the weekly Claude allowance as a reserve, usable only for security work and production incidents. Work of any other kind SHALL NOT draw on the reserve.

#### Scenario: Only emergencies use the reserve
- GIVEN only the reserve remains
- WHEN the next issue is requested
- THEN only security or incident work is offered

### Requirement: Work overflows within the cap

When the Claude allowance outside the reserve is used up, or a shorter limit is in force, the system SHALL route eligible work to overflow models as described in model-routing, while spending stays under the Requesty cap. At the cap, the factory SHALL ask a budget question as described in questions, and SHALL continue with whatever fuel remains or wait for the next reset.

#### Scenario: Reaching the cap
- GIVEN the Claude allowance outside the reserve is spent
- AND overflow spending reaches the Requesty cap
- WHEN more work is ready
- THEN a budget question is asked
- AND no issue is blocked by it

### Requirement: Spending is paced across the week

The system SHALL spread spending across the sprint so that no more than its share of the remaining budget is used before each remaining day, keeping work moving through the whole week rather than stalling after a burst.

#### Scenario: Early burst held back
- GIVEN two days into a sprint with 70 percent of the non-reserve allowance already used
- WHEN the next non-security issue is requested
- THEN it is not offered until spending is back within the pace
