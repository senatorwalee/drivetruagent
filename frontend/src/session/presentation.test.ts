import { describe, expect, it } from 'vitest'

import {
  clock,
  deriveAgentState,
  discountLabel,
  isFinal,
  itemsLabel,
  money,
  plainAmount,
  progressSteps,
  speakingMilliseconds,
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
