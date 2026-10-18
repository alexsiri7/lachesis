# Access

## Purpose

Lachesis is reached by MCP clients and a web browser on behalf of one author's account, and by the author's unattended scripts. Access has to be secure and has to survive the server restarting, which it does often.

## Requirements

### Requirement: Clients authenticate with OAuth

MCP clients and the web view SHALL authenticate through OAuth 2.1 with Google sign-in, or, for MCP clients, with a factory token. Requests without valid authentication SHALL be refused.

#### Scenario: Unauthenticated call
- GIVEN a client with no valid token
- WHEN it calls any tool
- THEN the call is refused and the client is directed to authenticate

### Requirement: Sessions survive restarts

After the server restarts, previously issued tokens that have not expired or been revoked SHALL keep working without the author re-authenticating. Expired access tokens SHALL be refreshed automatically from stored refresh tokens, including when the client has been idle.

#### Scenario: Deploy overnight
- GIVEN a client authenticated yesterday
- WHEN the server is redeployed and the client calls a tool this morning
- THEN the call succeeds without a new sign-in

### Requirement: Factory tokens let scripts act for the author

The author SHALL be able to create a named factory token, which is returned once and never again, and which an MCP client MAY present as its bearer token to act as the author until it is revoked. The system SHALL store only a hash of it.

#### Scenario: A script reports with its token
- GIVEN the author created the factory token cron
- WHEN a client presents it and calls read_fuel
- THEN the call succeeds as the author

### Requirement: Factory tokens are listed and revoked by name

The author SHALL be able to list factory tokens by name, creation and last use, without their values, and to revoke one by name, after which it SHALL be refused.

#### Scenario: Revoked token
- GIVEN the author revoked the factory token cron
- WHEN a client presents it and calls read_fuel
- THEN the call is refused

### Requirement: Only Google sign-in manages factory tokens

Only an author authenticated through Google sign-in SHALL be able to create or revoke factory tokens.

#### Scenario: A token cannot mint tokens
- GIVEN a client authenticated with a factory token
- WHEN it tries to create a factory token
- THEN it is refused
