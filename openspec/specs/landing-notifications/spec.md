# Landing Notifications

## Purpose

The author learns when a whole unit of work — a spec change, or a requirement file in a repository without specs — becomes usable, so they can go and try it. Notifications fire at the level of the unit of work, not an issue, and arrive as a push rather than something the author must check.

## Requirements

### Requirement: Notify when a unit of work lands

When every issue belonging to a unit of work, as defined in progress-reporting, has closed with a merged pull request and that work has reached production, the system SHALL send a push notification naming it and its repository, summarising in one line what is now available to use, and linking to it: to the spec change pull request, or to the requirement file. Partial completion SHALL NOT be notified.

#### Scenario: Last issue lands
- GIVEN a requirement with three linked issues, two already merged and deployed
- WHEN the third merges and reaches production
- THEN one notification is sent describing the new capability

#### Scenario: Partial landing
- GIVEN a requirement with four linked issues
- WHEN three have landed
- THEN no notification is sent

### Requirement: Production is defined per repository

Each registered repository SHALL record whether landing means a successful production deployment or a merge to the default branch, defaulting to merge. A notification for a merge-signal repository SHALL say it is merged, and SHALL NOT claim the work is live.

#### Scenario: Deployment signal
- GIVEN a repository whose landing signal is deployment
- WHEN a unit of work's final merge lands but has not deployed
- THEN no notification is sent until a deployment carrying it succeeds

### Requirement: Broken landings are notified

If a deployment carrying a unit of work's final merge fails, the system SHALL notify the author with the same urgency and a link to the failed deployment.

#### Scenario: Failed deploy
- GIVEN a unit of work's final merge
- WHEN the deployment carrying it fails
- THEN the author is notified with a link to the failed deployment

### Requirement: Each unit of work notifies once

A unit of work SHALL be notified at most once for landing. Re-running a deployment or a later merge touching the same unit of work SHALL NOT notify again.

#### Scenario: Redeploy
- GIVEN a unit of work already notified as landed
- WHEN the deployment is re-run
- THEN no new notification is sent

### Requirement: Delivery never blocks anything

Delivery SHALL be best effort. A notification that cannot be delivered SHALL be logged and dropped, and no requirement, issue or status operation SHALL depend on a notification being sent.

#### Scenario: Notification service down
- GIVEN the notification endpoint is unreachable
- WHEN a unit of work lands
- THEN every Lachesis operation continues to succeed

### Requirement: Notification settings are per account

The notification topic, server (defaulting to https://ntfy.sh) and optional access token SHALL be configured per account. An empty topic SHALL clear the configuration and make notifications inert. The stored configuration SHALL report whether a token is set and SHALL NOT return the token itself. The author SHALL be able to send a test notification and learn whether it was delivered.

#### Scenario: No topic configured
- GIVEN no topic is configured
- WHEN a unit of work lands
- THEN nothing is sent and Lachesis behaves as if the feature did not exist
