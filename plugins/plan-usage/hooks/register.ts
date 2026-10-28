import type { Register, EngineInterface, SessionRateLimit } from 'claude-code'

const TOOL = 'mcp__plan-usage__usage'

// How often the status line's countdowns are redrawn between measurements.
const STATUS_REFRESH_MS = 60_000

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
  // With pace on, the weekly cap is never below paceFloor early in the week, and
  // is paceSlack points over the linear cap after that, up to 100%.
  paceFloor: number
  paceSlack: number
  // Optional stepped weekly cap: [elapsedPercent, capPercent] pairs, e.g.
  // [[0, 65], [90, 90], [95, 100]] holds the week to 65% until 90% of it has
  // passed, then 90%, then 100% for the last 5%. Spare capacity most of the
  // week, all of it used by the end. Overrides weeklyCapPercent and pace.
  weeklySteps?: [number, number][]
  // Optional lift of the 5-hour cap at the end of the week: once the 7-day
  // window resets within `hours`, the 5-hour cap is `cap` instead of
  // fiveHourCapPercent, so the week's last allowance is not held back.
  fiveHourCapLastHours?: { hours: number; cap: number }
  // What a cap does. 'wrap-up' (the default) refuses nothing: past the cap every
  // tool result carries a reminder to finish the work in hand and start nothing
  // new, so no change is left half-made. 'stop' refuses every tool call but the
  // allowed few, for an account that must never go past its cap.
  mode: 'wrap-up' | 'stop'
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

function msToReset(w: SessionRateLimit, now: number): number | undefined {
  if (!w.resetsAt) return undefined
  const msLeft = Date.parse(w.resetsAt) - now
  return Number.isNaN(msLeft) ? undefined : msLeft
}

// How far through the window we are: time left to the reset and the share of
// the window's length already elapsed, so usage can be read against pace.
function windowTiming(
  w: SessionRateLimit,
  now: number,
): { left: string; elapsed: number; elapsedExact: number } | undefined {
  const length = WINDOW_MS[w.kind]
  const msLeft = msToReset(w, now)
  if (msLeft === undefined || !length) return undefined
  const elapsedExact = Math.min(100, Math.max(0, (1 - msLeft / length) * 100))
  return { left: formatSpan(msLeft), elapsed: Math.round(elapsedExact), elapsedExact }
}

// How much of the guard's cap a window has used: 50% used against a 60% cap is
// 83% of the cap. Undefined when there is no cap for the window.
export function shareOfCap(used: number, cap: number | undefined): number | undefined {
  if (cap === undefined || !(cap > 0)) return undefined
  return Math.round((used / cap) * 100)
}

function describeWindow(w: SessionRateLimit, now: number, cap?: number, pace?: number): string {
  const label = LABELS[w.kind] ?? w.kind
  const resets = w.resetsAt ? `, resets ${w.resetsAt}` : ''
  const t = windowTiming(w, now)
  const timing = t ? ` (in ${t.left}; ${t.elapsed}% of the window elapsed)` : ''
  const share = shareOfCap(w.percentUsed, cap)
  const ofCap = share !== undefined ? `; ${share}% of the ${cap}% cap` : ''
  const paceText = pace !== undefined ? `; ${describePace(pace)}` : ''
  return `${label}: ${w.percentUsed}% used${resets}${timing}${ofCap}${paceText}`
}

// "7d 38%/67% ↺2d 7h · 58% of 65% cap": used / elapsed, the time to the reset,
// then (with the guard on) how much of the window's cap is used.
function shortWindow(w: SessionRateLimit, now: number, cap?: number): string {
  const t = windowTiming(w, now)
  const name = SHORT[w.kind] ?? w.kind
  const base = t ? `${name} ${w.percentUsed}%/${t.elapsed}% ↺${t.left}` : `${name} ${w.percentUsed}%`
  const share = shareOfCap(w.percentUsed, cap)
  return share !== undefined ? `${base} · ${share}% of ${cap}% cap` : base
}

async function configDir($: EngineInterface): Promise<string> {
  return (await $.env.get('CLAUDE_CONFIG_DIR')) ?? `${await $.env.get('HOME')}/.claude`
}

// <config dir>/budget-guard.json, e.g.
// { "enabled": true, "weeklyCapPercent": 65, "fiveHourCapPercent": 60, "pace": true,
//   "paceFloor": 15, "paceSlack": 10, "fiveHourCapLastHours": { "hours": 5, "cap": 100 }, "mode": "wrap-up" }
// Missing or unreadable means the guard is off.
async function readGuard($: EngineInterface): Promise<GuardConfig | undefined> {
  try {
    const raw = JSON.parse(await $.fs.read(`${await configDir($)}/budget-guard.json`))
    if (raw.enabled !== true) return undefined
    return {
      enabled: true,
      weeklyCapPercent: points(raw.weeklyCapPercent, 100),
      fiveHourCapPercent: points(raw.fiveHourCapPercent, 100),
      pace: raw.pace === true,
      paceFloor: points(raw.paceFloor),
      paceSlack: points(raw.paceSlack),
      weeklySteps: parseSteps(raw.weeklySteps),
      fiveHourCapLastHours: parseLastHours(raw.fiveHourCapLastHours),
      mode: raw.mode === 'stop' ? 'stop' : 'wrap-up',
    }
  } catch {
    return undefined
  }
}

// Valid [elapsed, cap] pairs sorted by elapsed, or undefined when absent or empty.
function parseSteps(raw: unknown): [number, number][] | undefined {
  if (!Array.isArray(raw)) return undefined
  const steps = raw
    .filter(s => Array.isArray(s) && s.length === 2)
    .map(([e, c]) => [Number(e), Number(c)] as [number, number])
    .filter(([e, c]) => Number.isFinite(e) && Number.isFinite(c))
    .sort((a, b) => a[0] - b[0])
  return steps.length ? steps : undefined
}

// A number of points, the fallback when absent or not a number: a NaN cap would never trip.
function points(raw: unknown, fallback = 0): number {
  const n = Number(raw ?? fallback)
  return Number.isFinite(n) ? n : fallback
}

// { hours, cap } when both are numbers, else undefined.
function parseLastHours(raw: unknown): GuardConfig['fiveHourCapLastHours'] {
  if (typeof raw !== 'object' || raw === null) return undefined
  const { hours, cap } = raw as Record<string, unknown>
  if (hours === undefined || cap === undefined) return undefined
  const lift = { hours: Number(hours), cap: Number(cap) }
  return Number.isFinite(lift.hours) && Number.isFinite(lift.cap) ? lift : undefined
}

// The cap of the last step whose elapsed threshold has been reached.
function stepCap(steps: [number, number][], elapsed: number): number {
  let cap = steps[0]![1]
  for (const [e, c] of steps) if (elapsed >= e) cap = c
  return cap
}

// fiveHourCapLastHours while it is in force: the 7-day window resets within its
// hours. No 7-day reset time means the 5-hour cap is not lifted.
function fiveHourLift(
  guard: GuardConfig,
  windows: SessionRateLimit[],
  now: number,
): GuardConfig['fiveHourCapLastHours'] {
  const lift = guard.fiveHourCapLastHours
  const week = windows.find(w => w.kind === 'seven_day')
  const msLeft = lift && week ? msToReset(week, now) : undefined
  return lift && msLeft !== undefined && msLeft <= lift.hours * 3600_000 ? lift : undefined
}

// The cap in force for a window now, given every window read with it. The
// 5-hour cap is fiveHourCapLastHours' cap while it is lifted. With weeklySteps,
// the weekly cap is the step reached by the share of the week elapsed. With pace
// on, the weekly cap is weeklyCapPercent times the share of the week elapsed,
// raised to paceFloor and by paceSlack up to 100%, to two decimals; a window
// with no reset time falls back to the full cap.
function capFor(
  guard: GuardConfig,
  w: SessionRateLimit,
  windows: SessionRateLimit[],
  now: number,
): number | undefined {
  if (w.kind === 'five_hour') return fiveHourLift(guard, windows, now)?.cap ?? guard.fiveHourCapPercent
  if (w.kind !== 'seven_day') return undefined
  if (guard.weeklySteps) {
    const t = windowTiming(w, now)
    return t ? stepCap(guard.weeklySteps, t.elapsedExact) : guard.weeklySteps[0]![1]
  }
  const t = guard.pace ? windowTiming(w, now) : undefined
  if (!t) return guard.weeklyCapPercent
  const linear = linearPace(guard, t)
  // Floor and slack only ever raise the cap, and never past 100%.
  const raised = Math.min(100, Math.max(guard.paceFloor, linear + guard.paceSlack))
  return Math.round(Math.max(linear, raised) * 100) / 100
}

// The pace: the linear share of weeklyCapPercent for the week elapsed.
function linearPace(guard: GuardConfig, t: { elapsedExact: number }): number {
  return (guard.weeklyCapPercent * t.elapsedExact) / 100
}

// Points the week's use is over (positive) or under (negative) pace. Only a paced
// weekly cap has a pace, and only with a reset time.
function paceGap(guard: GuardConfig, w: SessionRateLimit, now: number): number | undefined {
  if (w.kind !== 'seven_day' || !guard.pace || guard.weeklySteps) return undefined
  const t = windowTiming(w, now)
  return t ? Math.round(w.percentUsed - linearPace(guard, t)) : undefined
}

function pointsText(n: number): string {
  return n === 1 ? '1 point' : `${n} points`
}

function describePace(gap: number): string {
  if (gap > 0) return `over pace by ${pointsText(gap)}`
  if (gap < 0) return `under pace by ${pointsText(-gap)}`
  return 'on pace'
}

// "under pace by N points" when the week's use is behind pace: allowance another
// session can spend. Undefined otherwise.
function spareCapacity(guard: GuardConfig, windows: SessionRateLimit[], now: number): string | undefined {
  const week = windows.find(w => w.kind === 'seven_day')
  const gap = week && paceGap(guard, week, now)
  return gap !== undefined && gap < 0 ? describePace(gap) : undefined
}

function describeCaps(guard: GuardConfig, windows: SessionRateLimit[], now: number): string {
  const lift = fiveHourLift(guard, windows, now)
  const fiveHour = lift
    ? `5-hour cap ${lift.cap}% now (lifted from ${guard.fiveHourCapPercent}% in the last ${lift.hours}h of the week)`
    : `5-hour cap ${guard.fiveHourCapPercent}%`
  const week = windows.find(w => w.kind === 'seven_day')
  if (guard.weeklySteps) {
    const steps = guard.weeklySteps.map(([e, c]) => `${c}% from ${e}%`).join(', ')
    const inForce = week ? ` ${capFor(guard, week, windows, now)}% now` : ''
    return `7-day cap${inForce} (stepped by week elapsed: ${steps}), ${fiveHour}`
  }
  const weekCap = week && guard.pace ? capFor(guard, week, windows, now) : undefined
  const weekly =
    weekCap !== undefined && weekCap !== guard.weeklyCapPercent
      ? `7-day cap ${weekCap}% now (${guard.weeklyCapPercent}% paced over the week${raisedBy(guard)})`
      : `7-day cap ${guard.weeklyCapPercent}%`
  return `${weekly}, ${fiveHour}`
}

// ", at least 15%, plus 10 points": what raises a paced cap, if anything.
function raisedBy(guard: GuardConfig): string {
  const floor = guard.paceFloor > 0 ? `, at least ${guard.paceFloor}%` : ''
  const slack = guard.paceSlack > 0 ? `, plus ${pointsText(guard.paceSlack)}` : ''
  return floor + slack
}

// The windows at or past their cap, as text; empty when within budget.
function breaches(guard: GuardConfig, windows: SessionRateLimit[], now: number): string[] {
  return windows.flatMap(w => {
    const cap = capFor(guard, w, windows, now)
    return cap !== undefined && w.percentUsed >= cap
      ? [`${LABELS[w.kind]} at ${w.percentUsed}% (cap ${cap}%)`]
      : []
  })
}

// What the model reads after each tool result while a window is past its cap.
export function wrapUpNote(over: string[]): string {
  return (
    `Budget guard, wrap-up mode: ${over.join('; ')}. Finish the change in hand and get it ` +
    'green, then merge it or leave a handoff note (add_note) with where it stands. Do not ' +
    'start new work: no new issue, branch or task. Then report usage and tell the user.'
  )
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
    // Between measurements nothing else redraws the status line, and its
    // countdowns would freeze at the last API response.
    $.clock.every(STATUS_REFRESH_MS, () => {
      $.session
        .usage()
        .then(usage => (usage.rateLimits.length ? showStatus($, usage.rateLimits) : undefined))
        .catch(() => undefined)
    })
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
    const guard = await readGuard($)
    const lines = usage.rateLimits.length
      ? usage.rateLimits.map(w =>
          guard
            ? describeWindow(w, now, capFor(guard, w, usage.rateLimits, now), paceGap(guard, w, now))
            : describeWindow(w, now),
        )
      : ['No rate-limit reading yet (none reported by the last API response).']
    if (usage.cost) lines.push(`Session cost so far: $${usage.cost.usd.toFixed(2)}`)
    if (usage.context.percent !== undefined) {
      lines.push(`Context window: ${usage.context.percent}% full`)
    }
    if (!guard) {
      lines.push('Budget guard: off')
    } else {
      const over = breaches(guard, usage.rateLimits, now)
      const spare = spareCapacity(guard, usage.rateLimits, now)
      lines.push(
        `Budget guard: on, ${guard.mode} mode (${describeCaps(guard, usage.rateLimits, now)})` +
          (over.length
            ? guard.mode === 'stop'
              ? ` - TRIPPED: ${over.join('; ')}`
              : ` - WRAP UP: ${over.join('; ')}. Finish what is in hand; start nothing new.`
            : spare
              ? ` - within budget, ${spare}: spare capacity available`
              : ' - within budget'),
      )
    }
    return { result: lines.join('\n') }
  }).catch($ => ({ deny: `${$.plugin.name}: could not read usage.` }))

  // The guard, while it is on and a window is at its cap. In wrap-up mode every
  // tool call runs and its result reminds the model to finish the work in hand
  // and start nothing new: stopping midway leaves worse damage than a small
  // overshoot. In stop mode every other tool call is refused.
  on('tool.call', async ($, e, next) => {
    if (ALWAYS_ALLOWED.some(re => re.test(e.tool))) return next(e)
    const guard = await readGuard($)
    if (!guard) return next(e)
    const over = breaches(guard, (await $.session.usage()).rateLimits, await $.clock.now())
    if (!over.length) return next(e)
    if (guard.mode === 'stop') {
      return {
        deny:
          `Budget guard: ${over.join('; ')}. Stop the budgeted work now: leave a ` +
          'handoff note on the issue (add_note), report usage, and tell the user. ' +
          'Only those tools are allowed until the window resets or the guard is turned off.',
      }
    }
    const result = await next(e)
    if (result.deny !== undefined || result.isError) return result
    return { ...result, context: [...(result.context ?? []), wrapUpNote(over)] }
  }).catch(($, e, next) => next(e)) // a guard that cannot read lets the work go on

  on('session.measure', async ($, e, next) => {
    await recordWindows($, e.rateLimits)
    await showStatus($, e.rateLimits)
    return next(e)
  })
}

// The status line: each window's use against time elapsed and the time to its
// reset, how much of its cap is used, and the guard's state. Redrawn on every
// measurement and every minute between them, so the countdown to each reset
// stays current.
async function showStatus($: EngineInterface, windows: SessionRateLimit[]): Promise<void> {
  const now = await $.clock.now()
  const guard = await readGuard($)
  if (!guard) {
    $.ui.status(windows.map(w => shortWindow(w, now)).join(' · ') || undefined)
    return
  }
  const fig = windows.map(w => shortWindow(w, now, capFor(guard, w, windows, now))).join(' | ')
  const over = breaches(guard, windows, now)
  const spare = spareCapacity(guard, windows, now)
  $.ui.status(
    !over.length
      ? `budget guard on${spare ? `, ${spare}` : ''} · ${fig}`
      : guard.mode === 'stop'
        ? `⛔ budget guard tripped: ${fig}`
        : `⚠ wrap up, start nothing new: ${fig}`,
  )
}
