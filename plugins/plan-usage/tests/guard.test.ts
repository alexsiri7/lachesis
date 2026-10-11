import { test, expect } from 'claude-code/testing'
import { wrapUpNote } from '../hooks/register'

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

function stepped(on: any, now: string, sevenDay: number) {
  on('clock.now', () => ({ value: Date.parse(now) }))
  on('env.get', () => ({ value: '/home/test' }))
  on('fs.read', () => ({
    value: JSON.stringify({ enabled: true, fiveHourCapPercent: 60, weeklySteps: [[0, 65], [90, 90], [95, 100]], mode: 'stop' }),
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
