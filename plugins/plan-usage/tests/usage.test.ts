import { test, expect } from 'claude-code/testing'

const TOOL = 'mcp__plan-usage__usage'

test('reports the rate-limit windows, cost and context fill', async ($, on) => {
  on('clock.now', () => ({ value: Date.parse('2026-10-06T09:00:00Z') }))
  on('session.usage', () => ({ value: {
    startedAt: 0,
    context: { window: 200000, tokens: 50000, percent: 25 },
    rateLimits: [
      { kind: 'five_hour', percentUsed: 23.5, resetsAt: '2026-10-06T12:00:00Z' },
      { kind: 'seven_day', percentUsed: 41 },
    ],
    cost: { usd: 1.234 },
  } }))
  const r = await $.tool.call({ tool: TOOL, input: {} })
  expect(r.result).toContain('5-hour window: 23.5% used, resets 2026-10-06T12:00:00Z')
  expect(r.result).toContain('7-day window: 41% used')
  expect(r.result).toContain('Session cost so far: $1.23')
  expect(r.result).toContain('Context window: 25% full')
})

test('says so when there is no rate-limit reading yet', async ($, on) => {
  on('clock.now', () => ({ value: Date.parse('2026-10-06T09:00:00Z') }))
  on('session.usage', () => ({ value: {
    startedAt: 0,
    context: { window: 200000 },
    rateLimits: [],
  } }))
  const r = await $.tool.call({ tool: TOOL, input: {} })
  expect(r.result).toContain('No rate-limit reading yet')
})

test('shows time to reset and how much of each window has elapsed', async ($, on) => {
  on('clock.now', () => ({ value: Date.parse('2026-10-08T08:00:00Z') }))
  on('session.usage', () => ({ value: {
    startedAt: 0,
    context: { window: 200000 },
    rateLimits: [
      { kind: 'five_hour', percentUsed: 9, resetsAt: '2026-10-08T10:30:00Z' },
      { kind: 'seven_day', percentUsed: 38, resetsAt: '2026-10-10T15:00:00Z' },
    ],
  } }))
  const r = await $.tool.call({ tool: TOOL, input: {} })
  expect(r.result).toContain('5-hour window: 9% used, resets 2026-10-08T10:30:00Z (in 2h 30m; 50% of the window elapsed)')
  expect(r.result).toContain('7-day window: 38% used, resets 2026-10-10T15:00:00Z (in 2d 7h; 67% of the window elapsed)')
})
