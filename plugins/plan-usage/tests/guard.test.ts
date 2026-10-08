import { test, expect } from 'claude-code/testing'

const GUARD_ON = JSON.stringify({ enabled: true, weeklyCapPercent: 65, fiveHourCapPercent: 60 })

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

test('guard on, 5-hour window at its cap: tools are refused', async ($, on) => {
  setup(on, GUARD_ON, 60, 20)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny ?? r.text).toContain('5-hour window at 60% (cap 60%)')
})

test('guard on, 7-day window past its cap: tools are refused', async ($, on) => {
  setup(on, GUARD_ON, 10, 70)
  const r = await $.tool.call({ tool: 'Bash', input: { command: 'ls' } })
  expect(r.deny ?? r.text).toContain('7-day window at 70% (cap 65%)')
})

test('guard tripped: the usage tool still answers and says so', async ($, on) => {
  setup(on, GUARD_ON, 10, 70)
  const r = await $.tool.call({ tool: 'mcp__plan-usage__usage', input: {} })
  expect(r.result).toContain('TRIPPED')
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
    return { value: JSON.stringify({ enabled: true, weeklyCapPercent: 70, fiveHourCapPercent: 60, pace: true }) }
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
