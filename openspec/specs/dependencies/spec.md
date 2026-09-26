# Dependencies

## Purpose

Dependencies express that one issue must land before another, so the autonomous worker builds things in an order that works. They live in GitHub, where they are visible without Lachesis.

## Requirements

### Requirement: An issue can be declared blocked by another

The system SHALL record that an issue is blocked by another issue in the same repository, and SHALL return the issue's updated dependency state. Dependencies SHALL be stored in GitHub itself and SHALL be visible there without Lachesis.

#### Scenario: Declare a dependency
- GIVEN issues 50 and 51
- WHEN 51 is declared blocked by 50
- THEN the result shows 51 blocked by 50
- AND the dependency is visible on GitHub

### Requirement: Dependencies can be read in both directions

For any issue, the system SHALL return the issues it is blocked by and the issues it is blocking.

#### Scenario: Both directions
- GIVEN 51 is blocked by 50
- WHEN the dependencies of 50 are read
- THEN 50 is shown as blocking 51
