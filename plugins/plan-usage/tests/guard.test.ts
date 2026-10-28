import { test, expect } from 'claude-code/testing'
import { shareOfCap, wrapUpNote } from '../hooks/register'

const GUARD_ON = JSON.stringify({ enabled: true, weeklyCapPercent: 65, fiveHourCapPercent: 60 })
const GUARD_STOP = JSON.stringify({ enabled: true, weeklyCapPercent: 65, fiveHourCapPercent: 60, mode: 'stop' })

function setup(on: any, guardFile: string | undefined, fiveHour: number, sevenDay: number) {
  on('env.get', () => ({ value: '/home/test' }))
  on('fs.read', () => (guardFile === undefined ? { deny: 'missing' } : { value: guardFile }))
  on('session.usage', () => ({
    value: {
      startedAt: 0,
      context: { window: 200000 },
      rateLimits: [
        { kind: 'five_hour', percentUsed: fiveHour },
        { kind: 'seven_day', percentUsed: sevenDay },
      ],
    },
  }))
  on('tool.call', { tool: 'Bash' }, () => ({ result: 'ran' }))
  on('clock.now', () => ({ value: Date.UTC(2026, 9, 6) }))
}

test('guard off: tools run whatever the usage', async ($, on) => {
  setup(on, undefined, 99, 99)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny).toBeUndefined()
})

test('guard on and within budget: tools run', async ($, on) => {
  setup(on, GUARD_ON, 59.9, 64)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny).toBeUndefined()
})

test('stop mode, 5-hour window at its cap: tools are refused', async ($, on) => {
  setup(on, GUARD_STOP, 60, 20)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny ?? r.text).toContain('5-hour window at 60% (cap 60%)')
})

test('stop mode, 7-day window past its cap: tools are refused', async ($, on) => {
  setup(on, GUARD_STOP, 10, 70)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny ?? r.text).toContain('7-day window at 70% (cap 65%)')
})

test('stop mode tripped: the usage tool still answers and says so', async ($, on) => {
  setup(on, GUARD_STOP, 10, 70)
  const r = await $.tool.call({ tool: 'mcp__plan-usage__usage', input: {} })
  expect(r.result).toContain('TRIPPED')
})

test('wrap-up mode is the default: past the cap tools still run', async ($, on) => {
  setup(on, GUARD_ON, 60, 20)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny).toBeUndefined()
  expect(r.result).toBe('ran')
})

test('wrap-up mode: the usage tool says to wrap up', async ($, on) => {
  setup(on, GUARD_ON, 10, 70)
  const r = await $.tool.call({ tool: 'mcp__plan-usage__usage', input: {} })
  expect(r.result).toContain('WRAP UP')
  expect(r.result).toContain('start nothing new')
})

test('guard disabled in its file: tools run', async ($, on) => {
  setup(on, JSON.stringify({ enabled: false, weeklyCapPercent: 1 }), 99, 99)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny).toBeUndefined()
})

test('a measurement records the windows to the config dir', async ($, on) => {
  const written: { path: string; text: string }[] = []
  on('env.get', (_$: any, e: any) => ({ value: e.name === 'CLAUDE_CONFIG_DIR' ? '/cfg' : undefined }))
  on('fs.read', () => ({ deny: 'missing' }))
  on('fs.write', (_$: any, e: any) => {
    written.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  on('clock.now', () => ({ value: Date.UTC(2026, 9, 6) }))
  on('session.measure', (_$: any, e: any) => ({ changed: e.changed }))
  on('ui.status', () => ({ value: undefined }))
  await $.session.measure({
    context: { window: 200000 },
    rateLimits: [{ kind: 'seven_day', percentUsed: 41, resetsAt: '2026-10-10T15:00:00Z' }],
    changed: ['rateLimits'],
  } as any)
  expect(written.map(w => w.path)).toEqual(['/cfg/plan-usage.json'])
  expect(JSON.parse(written[0]!.text).rateLimits[0].percentUsed).toBe(41)
})

function paced(on: any, sevenDay: number, readPath: string[] = []) {
  // Week resets 2026-10-10T15:00Z; at 2026-10-06T15:00Z three of its seven days have passed.
  on('clock.now', () => ({ value: Date.parse('2026-10-06T15:00:00Z') }))
  on('env.get', (_$: any, e: any) => ({ value: e.name === 'CLAUDE_CONFIG_DIR' ? '/factory-main' : '/home/test' }))
  on('fs.read', (_$: any, e: any) => {
    readPath.push(e.path)
    return { value: JSON.stringify({ enabled: true, weeklyCapPercent: 70, fiveHourCapPercent: 60, pace: true, mode: 'stop' }) }
  })
  on('session.usage', () => ({
    value: {
      startedAt: 0,
      context: { window: 200000 },
      rateLimits: [
        { kind: 'five_hour', percentUsed: 5, resetsAt: '2026-10-06T17:00:00Z' },
        { kind: 'seven_day', percentUsed: sevenDay, resetsAt: '2026-10-10T15:00:00Z' },
      ],
    },
  }))
  on('tool.call', { tool: 'Bash' }, () => ({ result: 'ran' }))
}

test('paced guard: the weekly cap is the cap times the share of the week elapsed', async ($, on) => {
  paced(on, 30.5)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny ?? r.text).toContain('7-day window at 30.5% (cap 30%)')
})

test('paced guard: under the paced cap, tools run', async ($, on) => {
  paced(on, 29)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny).toBeUndefined()
})

test('paced guard: the usage tool names the cap in force now', async ($, on) => {
  paced(on, 10)
  const r = await $.tool.call({ tool: 'mcp__plan-usage__usage', input: {} })
  expect(r.result).toContain('7-day cap 30% now (70% paced over the week)')
})

test('the guard file is read from the config dir', async ($, on) => {
  const paths: string[] = []
  paced(on, 10, paths)
  await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(paths).toContain('/factory-main/budget-guard.json')
})

function stepped(on: any, now: string, sevenDay: number, extra: object = {}) {
  on('clock.now', () => ({ value: Date.parse(now) }))
  on('env.get', () => ({ value: '/home/test' }))
  on('fs.read', () => ({
    value: JSON.stringify({
      enabled: true, fiveHourCapPercent: 60, weeklySteps: [[0, 65], [90, 90], [95, 100]], mode: 'stop', ...extra,
    }),
  }))
  on('session.usage', () => ({
    value: {
      startedAt: 0,
      context: { window: 200000 },
      rateLimits: [
        { kind: 'five_hour', percentUsed: 5 },
        { kind: 'seven_day', percentUsed: sevenDay, resetsAt: '2026-10-10T15:00:00Z' },
      ],
    },
  }))
  on('tool.call', { tool: 'Bash' }, () => ({ result: 'ran' }))
}

// Week resets 2026-10-10T15:00Z. 90% elapsed = 16.8h left; 95% = 8.4h left.
test('stepped guard: early in the week the first step holds', async ($, on) => {
  stepped(on, '2026-10-06T15:00:00Z', 66)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny ?? r.text).toContain('7-day window at 66% (cap 65%)')
})

test('stepped guard: past 90% of the week the cap rises to 90%', async ($, on) => {
  stepped(on, '2026-10-09T23:00:00Z', 85)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny).toBeUndefined()
})

test('stepped guard: past 95% of the week the cap is 100%', async ($, on) => {
  stepped(on, '2026-10-10T08:00:00Z', 95)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny).toBeUndefined()
})

test('stepped guard: the usage tool names the step in force', async ($, on) => {
  stepped(on, '2026-10-09T23:00:00Z', 10)
  const r = await $.tool.call({ tool: 'mcp__plan-usage__usage', input: {} })
  expect(r.result).toContain('7-day cap 90% now (stepped by week elapsed: 65% from 0%, 90% from 90%, 100% from 95%)')
})

test('the wrap-up note says to finish, not to start', async () => {
  const note = wrapUpNote(['5-hour window at 61% (cap 60%)'])
  expect(note).toContain('5-hour window at 61%')
  expect(note).toContain('Finish the change in hand')
  expect(note).toContain('Do not start new work')
})

test('share of cap: 50% used against a 60% cap is 83% of the cap', () => {
  expect(shareOfCap(50, 60)).toBe(83)
  expect(shareOfCap(50, undefined)).toBeUndefined()
  expect(shareOfCap(50, 0)).toBeUndefined()
})

test('the usage tool shows how much of each cap is used', async ($, on) => {
  setup(on, GUARD_ON, 30, 52)
  const r = await $.tool.call({ tool: 'mcp__plan-usage__usage', input: {} })
  expect(r.result).toContain('5-hour window: 30% used; 50% of the 60% cap')
  expect(r.result).toContain('7-day window: 52% used; 80% of the 65% cap')
})

test('the status line shows each cap and how much of it is used', async ($, on) => {
  const shown: (string | undefined)[] = []
  on('env.get', () => ({ value: '/home/test' }))
  on('fs.read', () => ({ value: GUARD_ON }))
  on('fs.write', () => ({ value: undefined }))
  on('clock.now', () => ({ value: Date.UTC(2026, 9, 6) }))
  on('session.measure', (_$: any, e: any) => ({ changed: e.changed }))
  on('ui.status', (_$: any, e: any) => {
    shown.push(e.text ?? e.value)
    return { value: undefined }
  })
  await $.session.measure({
    context: { window: 200000 },
    rateLimits: [
      { kind: 'five_hour', percentUsed: 30 },
      { kind: 'seven_day', percentUsed: 52 },
    ],
    changed: ['rateLimits'],
  } as any)
  expect(shown.at(-1)).toBe('budget guard on · 5h 30% · 50% of 60% cap | 7d 52% · 80% of 65% cap')
})

const LIFT = { enabled: true, fiveHourCapPercent: 60, fiveHourCapLastHours: { hours: 5, cap: 100 }, mode: 'stop' }

const WEEK_RESETS = '2026-10-10T15:00:00Z'

// The 5-hour window is at 70%.
function lifted(on: any, guard: object, now: string, weekResetsAt: string | undefined) {
  on('clock.now', () => ({ value: Date.parse(now) }))
  on('env.get', () => ({ value: '/home/test' }))
  on('fs.read', () => ({ value: JSON.stringify(guard) }))
  on('session.usage', () => ({
    value: {
      startedAt: 0,
      context: { window: 200000 },
      rateLimits: [
        { kind: 'five_hour', percentUsed: 70 },
        { kind: 'seven_day', percentUsed: 50, resetsAt: weekResetsAt },
      ],
    },
  }))
  on('tool.call', { tool: 'Bash' }, () => ({ result: 'ran' }))
}

test('5-hour lift absent: the 5-hour cap holds at the end of the week', async ($, on) => {
  const { fiveHourCapLastHours, ...guard } = LIFT
  lifted(on, guard, '2026-10-10T14:00:00Z', WEEK_RESETS)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny ?? r.text).toContain('5-hour window at 70% (cap 60%)')
})

test('5-hour lift: outside the last hours of the week the 5-hour cap holds', async ($, on) => {
  lifted(on, LIFT, '2026-10-10T09:00:00Z', WEEK_RESETS)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny ?? r.text).toContain('5-hour window at 70% (cap 60%)')
})

test('5-hour lift: in the last hours of the week the lifted cap is in force', async ($, on) => {
  lifted(on, LIFT, '2026-10-10T10:00:00Z', WEEK_RESETS) // exactly 5h to the reset
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny).toBeUndefined()
  const usage = await $.tool.call({ tool: 'mcp__plan-usage__usage', input: {} })
  expect(usage.result).toContain('5-hour window: 70% used; 70% of the 100% cap')
  expect(usage.result).toContain('5-hour cap 100% now (lifted from 60% in the last 5h of the week)')
})

test('5-hour lift: no 7-day reset time keeps the 5-hour cap', async ($, on) => {
  lifted(on, LIFT, '2026-10-10T14:00:00Z', undefined)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny ?? r.text).toContain('5-hour window at 70% (cap 60%)')
})

test('5-hour lift: the status line shows the lifted cap in the last hours of the week', async ($, on) => {
  const shown: (string | undefined)[] = []
  lifted(on, LIFT, '2026-10-10T12:00:00Z', WEEK_RESETS)
  on('fs.write', () => ({ value: undefined }))
  on('session.measure', (_$: any, e: any) => ({ changed: e.changed }))
  on('ui.status', (_$: any, e: any) => {
    shown.push(e.text ?? e.value)
    return { value: undefined }
  })
  await $.session.measure({
    context: { window: 200000 },
    rateLimits: [
      { kind: 'five_hour', percentUsed: 70 },
      { kind: 'seven_day', percentUsed: 50, resetsAt: WEEK_RESETS },
    ],
    changed: ['rateLimits'],
  } as any)
  expect(shown.at(-1)).toStartWith('budget guard on · ')
  expect(shown.at(-1)).toContain('5h 70% · 70% of 100% cap')
})

// Week resets 2026-10-10T15:00Z: 1h in at 2026-10-03T16:00Z, half way at 2026-10-07T03:00Z,
// 12h left at 2026-10-10T03:00Z.
function prorated(on: any, now: string, sevenDay: number, extra: object = {}) {
  on('clock.now', () => ({ value: Date.parse(now) }))
  on('env.get', () => ({ value: '/home/test' }))
  on('fs.read', () => ({
    value: JSON.stringify({
      enabled: true, fiveHourCapPercent: 60, pace: true, paceFloor: 15, paceSlack: 10, mode: 'stop', ...extra,
    }),
  }))
  on('session.usage', () => ({
    value: {
      startedAt: 0,
      context: { window: 200000 },
      rateLimits: [
        { kind: 'five_hour', percentUsed: 5 },
        { kind: 'seven_day', percentUsed: sevenDay, resetsAt: '2026-10-10T15:00:00Z' },
      ],
    },
  }))
  on('tool.call', { tool: 'Bash' }, () => ({ result: 'ran' }))
}

test('prorated guard: at the window start the floor holds', async ($, on) => {
  prorated(on, '2026-10-03T16:00:00Z', 16)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny ?? r.text).toContain('7-day window at 16% (cap 15%)')
})

test('prorated guard: half way through the week the cap is linear plus the slack', async ($, on) => {
  prorated(on, '2026-10-07T03:00:00Z', 61)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny ?? r.text).toContain('7-day window at 61% (cap 60%)')
})

test('prorated guard: at the window end the cap reaches 100%', async ($, on) => {
  prorated(on, '2026-10-10T03:00:00Z', 99)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny).toBeUndefined()
})

test('prorated guard: the usage tool names the cap, its floor and slack, and the pace', async ($, on) => {
  prorated(on, '2026-10-07T03:00:00Z', 30)
  const r = await $.tool.call({ tool: 'mcp__plan-usage__usage', input: {} })
  expect(r.result).toContain('7-day cap 60% now (100% paced over the week, at least 15%, plus 10 points)')
  expect(r.result).toContain('; under pace by 20 points')
  expect(r.result).toContain('within budget, under pace by 20 points: spare capacity available')
})

test('prorated guard: over pace is reported but offers no spare capacity', async ($, on) => {
  prorated(on, '2026-10-07T03:00:00Z', 55)
  const r = await $.tool.call({ tool: 'mcp__plan-usage__usage', input: {} })
  expect(r.result).toContain('; over pace by 5 points')
  expect(r.result).toContain('- within budget')
  expect(r.result).not.toContain('spare capacity')
})

test('prorated guard: the status line says when the week is under pace', async ($, on) => {
  const shown: (string | undefined)[] = []
  prorated(on, '2026-10-07T03:00:00Z', 30)
  on('fs.write', () => ({ value: undefined }))
  on('session.measure', (_$: any, e: any) => ({ changed: e.changed }))
  on('ui.status', (_$: any, e: any) => {
    shown.push(e.text ?? e.value)
    return { value: undefined }
  })
  await $.session.measure({
    context: { window: 200000 },
    rateLimits: [
      { kind: 'five_hour', percentUsed: 5 },
      { kind: 'seven_day', percentUsed: 30, resetsAt: '2026-10-10T15:00:00Z' },
    ],
    changed: ['rateLimits'],
  } as any)
  expect(shown.at(-1)).toStartWith('budget guard on, under pace by 20 points · ')
})

test('stepped guard: at the window start the first step holds', async ($, on) => {
  stepped(on, '2026-10-03T16:00:00Z', 66)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny ?? r.text).toContain('7-day window at 66% (cap 65%)')
})

test('stepped guard: no pace is reported', async ($, on) => {
  stepped(on, '2026-10-07T03:00:00Z', 10)
  const r = await $.tool.call({ tool: 'mcp__plan-usage__usage', input: {} })
  expect(r.result).not.toContain('pace by')
})

test('stepped guard: steps override pace, so no pace is reported even with pace on', async ($, on) => {
  stepped(on, '2026-10-07T03:00:00Z', 10, { pace: true, paceFloor: 15, paceSlack: 10 })
  const r = await $.tool.call({ tool: 'mcp__plan-usage__usage', input: {} })
  expect(r.result).toContain('7-day cap 65% now (stepped by week elapsed')
  expect(r.result).not.toContain('pace by')
  expect(r.result).not.toContain('on pace')
})

test('prorated guard: a weekly cap that is not a number counts as 100%', async ($, on) => {
  prorated(on, '2026-10-07T03:00:00Z', 30, { weeklyCapPercent: 'high' })
  const r = await $.tool.call({ tool: 'mcp__plan-usage__usage', input: {} })
  expect(r.result).toContain('7-day cap 60% now (100% paced over the week, at least 15%, plus 10 points)')
  expect(r.result).toContain('within budget, under pace by 20 points: spare capacity available')
})
