# Repositories

## Purpose

Lachesis only works on repositories the author has registered. The registry decides what cross-repository views and pickup cover, and holds per-repository settings. The author can also start a brand-new project from Lachesis, so an idea can go from conversation to a repository ready for spec changes in one step.

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

### Requirement: A new project can be created in one step

The system SHALL let the author create a new project by giving a repository name, a purpose, and optionally one or more initial capabilities. In one operation it SHALL create the GitHub repository under the author's account, seed its default branch with openspec/specs containing the given capabilities, and register the repository. When no capabilities are given, the system SHALL seed a single capability whose Purpose is the given purpose.

#### Scenario: Create with initial capabilities
- GIVEN no repository named "name-checker" exists in the author's account
- WHEN the author creates a project "name-checker" with a purpose and two capabilities
- THEN the repository exists on GitHub
- AND its default branch contains openspec/specs with exactly those two capabilities
- AND the repository is registered with Lachesis

#### Scenario: Create with purpose only
- GIVEN no repository named "name-checker" exists in the author's account
- WHEN the author creates a project "name-checker" with only a purpose
- THEN its openspec/specs contains one capability whose Purpose is that purpose

### Requirement: A created project is ready for the spec-change workflow

A project created through Lachesis SHALL use specs from the moment it is created, so the author can list its capabilities and propose a spec change without further setup.

#### Scenario: Immediately usable
- GIVEN a project was just created through Lachesis
- WHEN the author lists its capabilities
- THEN uses_specs is true and its seeded capabilities are listed
- AND a spec change can be proposed against it

### Requirement: New projects are private by default

A project created through Lachesis SHALL be a private repository unless the author asks for a public one.

#### Scenario: Default visibility
- GIVEN the author creates a project without stating visibility
- WHEN the repository is created
- THEN it is private

### Requirement: Creation fails cleanly

If a project cannot be created, the system SHALL refuse with the reason and SHALL leave no partial state: no repository created, no files written and nothing registered. A name that already exists in the author's account SHALL be refused before anything is created.

#### Scenario: Name already taken
- GIVEN a repository named "filmduel" already exists in the author's account
- WHEN the author creates a project "filmduel"
- THEN the request is refused, saying the name is taken
- AND nothing is created or registered

#### Scenario: Invalid capability
- GIVEN an initial capability that fails the spec format check
- WHEN the author creates a project with it
- THEN the request is refused listing the format problems
- AND no repository is created
