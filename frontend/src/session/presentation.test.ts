import { describe, expect, it } from 'vitest'

import {
  clock,
  deriveAgentState,
  discountLabel,
  modelCost,
  isFinal,
  itemsLabel,
  money,
  plainAmount,
  progressSteps,
  runningSeconds,
  secondsUntil,
  speakingMilliseconds,
  timerNote,
} from './presentation'

const quiet = { paused: false, ended: false, waitingForReply: false, speaking: false, customerActive: false }

describe('money', () => {
  it.each([
    [0, '$0.00'],
    [5, '$0.05'],
    [379, '$3.79'],
    [1035, '$10.35'],
    [120000, '$1200.00'],
  ])('%i cents is %s', (cents, expected) => {
    expect(money(cents)).toBe(expected)
  })
})

describe('plainAmount', () => {
  it('prints cents as a till does, with no currency sign', () => {
    expect([0, 5, 200, 1035].map(plainAmount)).toEqual(['0.00', '0.05', '2.00', '10.35'])
  })
})

describe('discountLabel', () => {
  it('is the name alone when the saving applied once', () => {
    expect(discountLabel({ name: 'Burger combo', times: 1 })).toBe('Burger combo')
  })

  it('says how many times when it applied more than once', () => {
    expect(discountLabel({ name: 'Burger combo', times: 3 })).toBe('Burger combo × 3')
  })
})

const START = '2026-10-13T12:00:00Z'
const at = (seconds: number) => Date.parse(START) + seconds * 1000
const iso = (seconds: number) => new Date(at(seconds)).toISOString()
const timers = (overrides: Partial<Parameters<typeof runningSeconds>[0]['timers']> = {}) => ({
  paused: false,
  paused_at: null,
  paused_seconds: 0,
  nudge_at: iso(20),
  closes_at: null,
  hand_off_at: iso(180),
  ...overrides,
})

describe('runningSeconds', () => {
  const session = (overrides = {}, ended_at: string | null = null) => ({ started_at: START, ended_at, timers: timers(overrides) })

  it('is the time since the session started', () => {
    expect(runningSeconds(session(), at(42))).toBe(42)
  })

  it('leaves out time spent paused', () => {
    expect(runningSeconds(session({ paused_seconds: 30 }), at(100))).toBe(70)
  })

  it('stands still while paused', () => {
    const paused = session({ paused: true, paused_at: iso(50), paused_seconds: 10 })

    expect(runningSeconds(paused, at(60))).toBe(40)
    expect(runningSeconds(paused, at(6000))).toBe(40)
  })

  it('stops when the session ends', () => {
    expect(runningSeconds(session({ paused_seconds: 5 }, iso(90)), at(500))).toBe(85)
  })

  it('is never negative', () => {
    expect(runningSeconds(session(), at(-5))).toBe(0)
  })
})

describe('secondsUntil', () => {
  it('counts down to a time the backend gave', () => {
    expect(secondsUntil(iso(180), at(42))).toBe(138)
  })

  it('stops at zero once the time has passed', () => {
    expect(secondsUntil(iso(20), at(25))).toBe(0)
  })

  it('is null when the backend gave no time', () => {
    expect(secondsUntil(null, at(0))).toBeNull()
    expect(secondsUntil(undefined, at(0))).toBeNull()
  })
})

describe('timerNote', () => {
  it('counts down to the hand-off while the session is running', () => {
    expect(timerNote(timers(), at(42))).toBe('hand-off in 2:18')
  })

  it('counts down to closing once the customer has been asked', () => {
    expect(timerNote(timers({ nudge_at: null, closes_at: iso(40) }), at(26.5))).toBe('closes in 0:14')
  })

  it('says the timers are paused, and counts down to nothing', () => {
    const paused = timers({ paused: true, paused_at: iso(30), nudge_at: null, hand_off_at: null })

    expect(timerNote(paused, at(500))).toBe('timers paused')
  })

  it('says nothing when no timer is running', () => {
    expect(timerNote(timers({ nudge_at: null, hand_off_at: null }), at(0))).toBeNull()
    expect(timerNote(undefined, at(0))).toBeNull()
  })

  it('rounds up, so the last second reads 0:01 and not 0:00', () => {
    expect(timerNote(timers(), at(179.4))).toBe('hand-off in 0:01')
  })
})

describe('clock', () => {
  it.each([
    [0, '0:00'],
    [9.7, '0:09'],
    [42, '0:42'],
    [60, '1:00'],
    [185, '3:05'],
    [-3, '0:00'],
  ])('%f seconds is %s', (seconds, expected) => {
    expect(clock(seconds)).toBe(expected)
  })
})

describe('itemsLabel', () => {
  it('uses the singular for one', () => {
    expect([itemsLabel(0), itemsLabel(1), itemsLabel(4)]).toEqual(['0 items', '1 item', '4 items'])
  })
})

describe('deriveAgentState', () => {
  it('is ready when nothing is happening', () => {
    expect(deriveAgentState(quiet)).toBe('ready')
  })

  it('is listening while the customer is typing or talking', () => {
    expect(deriveAgentState({ ...quiet, customerActive: true })).toBe('listening')
  })

  it('is working while waiting for the reply', () => {
    expect(deriveAgentState({ ...quiet, waitingForReply: true, customerActive: true })).toBe('working')
  })

  it('is speaking once the reply arrives', () => {
    expect(deriveAgentState({ ...quiet, speaking: true })).toBe('speaking')
  })

  it('finishes speaking the closing line before showing the session as ended', () => {
    expect(deriveAgentState({ ...quiet, ended: true, speaking: true })).toBe('speaking')
    expect(deriveAgentState({ ...quiet, ended: true })).toBe('ended')
  })

  it('shows paused whatever else is going on', () => {
    expect(deriveAgentState({ paused: true, ended: true, waitingForReply: true, speaking: true, customerActive: true })).toBe(
      'paused',
    )
  })
})

describe('speakingMilliseconds', () => {
  it('lasts longer for longer replies, within sensible bounds', () => {
    const short = speakingMilliseconds('Hi!')
    const medium = speakingMilliseconds('Large fries and a medium cola. Anything else?')
    const long = speakingMilliseconds('x'.repeat(2000))

    expect(short).toBe(1500)
    expect(medium).toBeGreaterThan(short)
    expect(long).toBe(6000)
  })
})

describe('progressSteps', () => {
  const statuses = (steps: ReturnType<typeof progressSteps>) => steps.map((step) => `${step.label}:${step.status}`)

  it('marks the steps of a normal order', () => {
    expect(statuses(progressSteps('ordering', true))).toEqual([
      'Greeting:done',
      'Ordering:current',
      'Confirming:upcoming',
      'Submitted:upcoming',
    ])
  })

  it('ends on Submitted when the order is placed', () => {
    expect(statuses(progressSteps('submitted', true)).at(-1)).toBe('Submitted:current')
  })

  it('shows how a session ended when it was handed to staff', () => {
    expect(statuses(progressSteps('handed_off', true))).toEqual(['Greeting:done', 'Ordering:done', 'Handed to staff:current'])
  })

  it('does not claim ordering happened if it ended at the greeting', () => {
    expect(statuses(progressSteps('abandoned', false))).toEqual(['Greeting:done', 'Abandoned:current'])
  })
})

describe('isFinal', () => {
  it('is true only for states a session cannot leave', () => {
    expect((['greeting', 'ordering', 'confirming'] as const).map(isFinal)).toEqual([false, false, false])
    expect((['submitted', 'handed_off', 'abandoned', 'terminated'] as const).map(isFinal)).toEqual([true, true, true, true])
  })
})

describe('modelCost', () => {
  const cost = (micro_usd: number, unpriced_calls = 0, estimated_calls = 0) => ({ micro_usd, unpriced_calls, estimated_calls })

  it('shows fractions of a cent, cents, then dollars', () => {
    expect(modelCost(cost(0))).toBe('0.00¢')
    expect(modelCost(cost(2_689))).toBe('0.27¢')
    expect(modelCost(cost(18_340))).toBe('1.8¢')
    expect(modelCost(cost(1_234_000))).toBe('$1.23')
  })

  it('says when part of it is an estimate, or has no price', () => {
    expect(modelCost(cost(18_340, 0, 1))).toBe('≈1.8¢')
    expect(modelCost(cost(18_340, 2))).toBe('1.8¢ + 2 unpriced')
  })
})
