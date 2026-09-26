# Repositories

## Purpose

Lachesis only works on repositories the author has registered. The registry decides what cross-repository views and pickup cover, and holds per-repository settings.

## Requirements

### Requirement: Repositories are registered explicitly

The system SHALL let the author register, unregister and list repositories. Cross-repository operations SHALL cover exactly the registered repositories.

#### Scenario: Unregistered repository
- GIVEN a repository that is not registered
- WHEN the dashboard is requested
- THEN that repository does not appear

### Requirement: Re-registering keeps settings

Registering an already registered repository without giving a landing signal SHALL leave its existing landing signal unchanged.

#### Scenario: Re-register
- GIVEN a repository registered with landing signal "deployment"
- WHEN it is registered again with no landing signal
- THEN its landing signal is still "deployment"
