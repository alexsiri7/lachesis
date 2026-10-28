# plan-usage

A Claude Code plugin for the accounts Lachesis budgets.

- **`usage` tool** (`mcp__plan-usage__usage`): each rate-limit window's use, when it resets, how long that is from now and how much of the window has elapsed, plus session cost, context fill and the budget guard's state.
- **Status line**: `7d 38%/67% ↺2d 7h · 5h 9%/50% ↺2h 30m` — used / elapsed, then time to reset. Used below elapsed is on pace. With the budget guard on, each window also shows its cap and how much of it is used: `budget guard on · 5h 30% · 50% of 60% cap | 7d 52% · 80% of 65% cap` (52% used against a 65% cap is 80% of the cap). The usage tool reports the same figure.
- **Budget guard**: with `<config dir>/budget-guard.json` (`CLAUDE_CONFIG_DIR`, else `~/.claude`) set to `{ "enabled": true, "weeklyCapPercent": 65, "fiveHourCapPercent": 60 }`, a window at its cap puts the session in **wrap-up mode**: nothing is refused, but every tool result carries a reminder to finish the change in hand, merge it or leave a handoff note, and start nothing new, and the status line says to wrap up. Stopping midway leaves worse damage than a small overshoot. Add `"mode": "stop"` for the old hard stop: every tool call is refused at the cap except the usage tool and Lachesis's `add_note`, `report_usage`, `report_fuel` and `read_fuel`. Add `"pace": true` to grow the weekly cap with the week: the cap in force is `weeklyCapPercent` × the share of the 7-day window elapsed (65% cap, 10% of the week gone → 6.5%), so the allowance is not spent on day one. Add `"paceFloor"` and `"paceSlack"` to raise the paced cap: with `"weeklyCapPercent": 100, "paceFloor": 15, "paceSlack": 10` the cap is at least 15% early in the week and 10 points over the linear cap after that, never above 100%. With pace on, the usage tool says how far the week's use is over or under pace (the linear cap, without floor or slack), and while it is under, the guard's line and the status line say `under pace by N points` — spare capacity another session can use. Or add `"weeklySteps": [[0, 65], [90, 90], [95, 100]]` for a stepped weekly cap — `[share of week elapsed, cap]` pairs, so the week is held to 65% until 90% of it has passed, then 90%, then 100% for the last 5%: spare capacity most of the week, all of it used by the end. Steps override `weeklyCapPercent` and `pace`, with its floor and slack. Add `"fiveHourCapLastHours": { "hours": 5, "cap": 100 }` to lift the 5-hour cap at the end of the week: once the 7-day window resets within `hours`, the 5-hour cap in force is `cap` instead of `fiveHourCapPercent`, so a cap that guards the week against one burst does not hold back its last allowance. Without a 7-day reset time the 5-hour cap holds; the usage tool says when it is lifted.
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
