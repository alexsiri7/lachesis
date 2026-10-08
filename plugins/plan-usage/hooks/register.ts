import type { Register, EngineInterface, SessionRateLimit } from 'claude-code'

const TOOL = 'mcp__plan-usage__usage'

// Tools the guard never blocks, so a capped run can still check usage and
// leave Lachesis a handoff note and usage report before it stops.
const ALWAYS_ALLOWED = [
  /^mcp__plan-usage__usage$/,
  /lachesis__(add_note|report_usage|report_fuel|read_fuel)$/,
]

const LABELS: Record<string, string> = {
  five_hour: '5-hour window',
  seven_day: '7-day window',
  spend_limit: 'Spend limit',
}

type GuardConfig = {
  enabled: boolean
  weeklyCapPercent: number
  fiveHourCapPercent: number
  // When true the weekly cap grows with the week: weeklyCapPercent times the
  // share of the 7-day window elapsed, so the cap is not spent on day one.
  pace: boolean
}

const WINDOW_MS: Record<string, number> = {
  five_hour: 5 * 3600_000,
  seven_day: 7 * 24 * 3600_000,
}

const SHORT: Record<string, string> = { five_hour: '5h', seven_day: '7d' }

// "2d 7h", "3h 10m", "25m": how long until the window resets.
function formatSpan(ms: number): string {
  const mins = Math.max(0, Math.round(ms / 60_000))
  const d = Math.floor(mins / 1440)
  const h = Math.floor((mins % 1440) / 60)
  const m = mins % 60
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  return `${m}m`
}

// How far through the window we are: time left to the reset and the share of
// the window's length already elapsed, so usage can be read against pace.
function windowTiming(
  w: SessionRateLimit,
  now: number,
): { left: string; elapsed: number; elapsedExact: number } | undefined {
  const length = WINDOW_MS[w.kind]
  if (!w.resetsAt || !length) return undefined
  const msLeft = Date.parse(w.resetsAt) - now
  if (Number.isNaN(msLeft)) return undefined
  const elapsedExact = Math.min(100, Math.max(0, (1 - msLeft / length) * 100))
  return { left: formatSpan(msLeft), elapsed: Math.round(elapsedExact), elapsedExact }
}

function describeWindow(w: SessionRateLimit, now: number): string {
  const label = LABELS[w.kind] ?? w.kind
  const resets = w.resetsAt ? `, resets ${w.resetsAt}` : ''
  const t = windowTiming(w, now)
  const timing = t ? ` (in ${t.left}; ${t.elapsed}% of the window elapsed)` : ''
  return `${label}: ${w.percentUsed}% used${resets}${timing}`
}

// "7d 38%/67% ↺2d 7h": used / elapsed, then time to the reset.
function shortWindow(w: SessionRateLimit, now: number): string {
  const t = windowTiming(w, now)
  const name = SHORT[w.kind] ?? w.kind
  return t ? `${name} ${w.percentUsed}%/${t.elapsed}% ↺${t.left}` : `${name} ${w.percentUsed}%`
}

async function configDir($: EngineInterface): Promise<string> {
  return (await $.env.get('CLAUDE_CONFIG_DIR')) ?? `${await $.env.get('HOME')}/.claude`
}

// <config dir>/budget-guard.json, e.g.
// { "enabled": true, "weeklyCapPercent": 65, "fiveHourCapPercent": 60, "pace": true }
// Missing or unreadable means the guard is off.
async function readGuard($: EngineInterface): Promise<GuardConfig | undefined> {
  try {
    const raw = JSON.parse(await $.fs.read(`${await configDir($)}/budget-guard.json`))
    if (raw.enabled !== true) return undefined
    return {
      enabled: true,
      weeklyCapPercent: Number(raw.weeklyCapPercent ?? 100),
      fiveHourCapPercent: Number(raw.fiveHourCapPercent ?? 100),
      pace: raw.pace === true,
    }
  } catch {
    return undefined
  }
}

// The cap in force for a window now. With pace on, the weekly cap is
// weeklyCapPercent times the share of the week elapsed, to one decimal; a
// window with no reset time falls back to the full cap.
function capFor(guard: GuardConfig, w: SessionRateLimit, now: number): number | undefined {
  if (w.kind === 'five_hour') return guard.fiveHourCapPercent
  if (w.kind !== 'seven_day') return undefined
  const t = guard.pace ? windowTiming(w, now) : undefined
  if (!t) return guard.weeklyCapPercent
  return Math.round(guard.weeklyCapPercent * t.elapsedExact) / 100
}

function describeCaps(guard: GuardConfig, windows: SessionRateLimit[], now: number): string {
  const week = windows.find(w => w.kind === 'seven_day')
  const weekCap = week && guard.pace ? capFor(guard, week, now) : undefined
  const weekly =
    weekCap !== undefined && weekCap !== guard.weeklyCapPercent
      ? `7-day cap ${weekCap}% now (${guard.weeklyCapPercent}% paced over the week)`
      : `7-day cap ${guard.weeklyCapPercent}%`
  return `${weekly}, 5-hour cap ${guard.fiveHourCapPercent}%`
}

// The windows at or past their cap, as text; empty when within budget.
function breaches(guard: GuardConfig, windows: SessionRateLimit[], now: number): string[] {
  return windows.flatMap(w => {
    const cap = capFor(guard, w, now)
    return cap !== undefined && w.percentUsed >= cap
      ? [`${LABELS[w.kind]} at ${w.percentUsed}% (cap ${cap}%)`]
      : []
  })
}

// The latest windows, written to <config dir>/plan-usage.json after every measurement, so
// something outside Claude Code (a cron reporting fuel) can read this account's usage
// without spending tokens. The config dir is CLAUDE_CONFIG_DIR, else ~/.claude.
async function recordWindows($: EngineInterface, windows: SessionRateLimit[]): Promise<void> {
  if (!windows.length) return
  try {
    const dir = await configDir($)
    const record = { recordedAt: new Date(await $.clock.now()).toISOString(), rateLimits: windows }
    await $.fs.write(`${dir}/plan-usage.json`, JSON.stringify(record, null, 2) + '\n')
  } catch {
    // Recording is best effort: it never gets in the way of the session.
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.tool.register({
      name: 'usage',
      description:
        "Reads the user's Claude plan usage: how much of each rate-limit window " +
        "(5-hour, 7-day) is used and when it resets, plus this session's cost, " +
        'context-window fill and the budget guard. Use when the user asks about ' +
        'usage, limits or tokens, and before starting budgeted work.',
      inputSchema: { type: 'object', properties: {} },
    })
    return next(e)
  })

  on('tool.call', { tool: TOOL }, async $ => {
    const usage = await $.session.usage()
    const now = await $.clock.now()
    const lines = usage.rateLimits.length
      ? usage.rateLimits.map(w => describeWindow(w, now))
      : ['No rate-limit reading yet (none reported by the last API response).']
    if (usage.cost) lines.push(`Session cost so far: $${usage.cost.usd.toFixed(2)}`)
    if (usage.context.percent !== undefined) {
      lines.push(`Context window: ${usage.context.percent}% full`)
    }
    const guard = await readGuard($)
    if (!guard) {
      lines.push('Budget guard: off')
    } else {
      const over = breaches(guard, usage.rateLimits, now)
      lines.push(
        `Budget guard: on (${describeCaps(guard, usage.rateLimits, now)})` +
          (over.length ? ` - TRIPPED: ${over.join('; ')}` : ' - within budget'),
      )
    }
    return { result: lines.join('\n') }
  }).catch($ => ({ deny: `${$.plugin.name}: could not read usage.` }))

  // The guard: while it is on and a window is at its cap, every other tool
  // call is refused, so budgeted work stops however the model reasons.
  on('tool.call', async ($, e, next) => {
    if (ALWAYS_ALLOWED.some(re => re.test(e.tool))) return next(e)
    const guard = await readGuard($)
    if (!guard) return next(e)
    const over = breaches(guard, (await $.session.usage()).rateLimits, await $.clock.now())
    if (!over.length) return next(e)
    return {
      deny:
        `Budget guard: ${over.join('; ')}. Stop the budgeted work now: leave a ` +
        'handoff note on the issue (add_note), report usage, and tell the user. ' +
        'Only those tools are allowed until the window resets or the guard is turned off.',
    }
  }).catch(($, e, next) => (next.called ? next(e) : { deny: `${$.plugin.name}: budget guard failed; refusing to be safe.` }))

  on('session.measure', async ($, e, next) => {
    await recordWindows($, e.rateLimits)
    const now = await $.clock.now()
    const fig = e.rateLimits.map(w => shortWindow(w, now)).join(' · ')
    const guard = await readGuard($)
    if (guard) {
      const over = breaches(guard, e.rateLimits, now)
      $.ui.status(over.length ? `⛔ budget guard tripped: ${fig}` : `budget guard on · ${fig}`)
    } else {
      $.ui.status(fig || undefined)
    }
    return next(e)
  })
}
