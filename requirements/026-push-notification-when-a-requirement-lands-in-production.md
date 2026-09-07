---
created: '2026-09-07'
github_issue: 113
id: '026'
status: idea
title: Push notification when a requirement lands in production
updated: '2026-09-07'
---

## Why

The author does not currently learn when work reaches production. Archon implements overnight and merges autonomously, so features arrive silently and are discovered by accident, sometimes weeks later. That delays the only review that matters, which is the author using the thing and forming a view on whether it was built right.

GitHub notifications do not solve this. They fire per issue and per PR, at a granularity far below the unit the author cares about, which is a whole requirement becoming usable. The signal is buried in volume and gets filtered out.

Email is the wrong channel for the same reason a dashboard is: both require the author to go and look. A push notification arrives, which matters when the whole problem is that the author does not know there is anything to look at.

## What

**The trigger**

When every issue linked to a requirement has closed with a merged PR, and that merge has reached production, the author receives a push notification.

Production means a successful deployment, not a merge. Where a repo reports deployment status, that is the signal. Where it does not, merge to the default branch stands in, and the repo's configuration records which of the two is in use so the notification never claims something is live when it is only merged.

**The notification**

Delivered to an ntfy topic configured per account. It carries the requirement's title and repo, a one-line summary of what is now available to use, and a link to the requirement. The point is to prompt the author to go and exercise the feature, so the text describes the capability rather than the issues that built it.

Partial completion is not notified. A requirement with four issues where three have landed is still in progress, and a notification per issue would train the author to ignore them.

**Failure is notified too**

A requirement is equally interesting when it lands broken. If a deployment carrying a requirement's final merge fails, the author is notified with the same urgency and a link to the failed deployment. Silence must mean nothing happened, never that something happened and went wrong.

**Delivery is best-effort and never blocking**

A notification that cannot be delivered is logged and dropped. Nothing about requirement or issue state depends on a notification having been sent, and a failing ntfy endpoint never blocks a merge, a status transition, or any other Lachesis operation.

Each requirement notifies once. Re-running a deploy, or a later unrelated merge touching the same requirement, does not re-notify.

**Configuration**

The ntfy topic and server are configured per account and can be changed or cleared. With no topic configured the feature is inert, and Lachesis behaves exactly as it does today.

## Issues

- #113 — ntfy configuration and best-effort delivery
- #114 — Detect requirement landing and fire the notification