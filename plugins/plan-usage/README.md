# plan-usage

A Claude Code plugin for the accounts Lachesis budgets.

- **`usage` tool** (`mcp__plan-usage__usage`): each rate-limit window's use, when it resets, how long that is from now and how much of the window has elapsed, plus session cost, context fill and the budget guard's state.
- **Status line**: `7d 38%/67% ↺2d 7h · 5h 9%/50% ↺2h 30m` — used / elapsed, then time to reset. Used below elapsed is on pace.
- **Budget guard**: with `<config dir>/budget-guard.json` (`CLAUDE_CONFIG_DIR`, else `~/.claude`) set to `{ "enabled": true, "weeklyCapPercent": 65, "fiveHourCapPercent": 60 }`, a window at its cap puts the session in **wrap-up mode**: nothing is refused, but every tool result carries a reminder to finish the change in hand, merge it or leave a handoff note, and start nothing new, and the status line says to wrap up. Stopping midway leaves worse damage than a small overshoot. Add `"mode": "stop"` for the old hard stop: every tool call is refused at the cap except the usage tool and Lachesis's `add_note`, `report_usage`, `report_fuel` and `read_fuel`. Add `"pace": true` to grow the weekly cap with the week: the cap in force is `weeklyCapPercent` × the share of the 7-day window elapsed (65% cap, 10% of the week gone → 6.5%), so the allowance is not spent on day one.
- **`plan-usage.json`**: the latest windows, written to the config dir (`CLAUDE_CONFIG_DIR`, else `~/.claude`) after every measurement, for the fuel-reporting cron.

## Install

This repository is a plugin marketplace:

```sh
claude plugin marketplace add alexsiri7/lachesis
claude plugin install plan-usage@lachesis
```

The repository is private, so the machine needs git credentials that can clone it. Turn on auto-update for the `lachesis` marketplace in `/plugin` → Marketplaces, or run `claude plugin marketplace update lachesis && claude plugin update plan-usage@lachesis`.

## Test

```sh
claude plugin validate plugins/plan-usage
claude plugin test plugins/plan-usage
```
