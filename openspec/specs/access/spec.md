# Access

## Purpose

Lachesis is reached by MCP clients and a web browser on behalf of one author's account. Access has to be secure and has to survive the server restarting, which it does often.

## Requirements

### Requirement: Clients authenticate with OAuth

MCP clients and the web view SHALL authenticate through OAuth 2.1 with Google sign-in. Requests without valid authentication SHALL be refused.

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
