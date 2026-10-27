import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import { IDLE } from '../voice/voiceCall'
import {
  callDuration,
  describeLineProblem,
  initials,
  keyFromHash,
  speakingLine,
  statusLine,
  turnedAwayLine,
} from './words'

describe('keyFromHash', () => {
  it('reads the key after the #', () => {
    expect(keyFromHash('#key=abc123')).toBe('abc123')
    expect(keyFromHash('#other=1&key=abc123')).toBe('abc123')
  })

  it('is null with no key, or an empty one', () => {
    expect(keyFromHash('')).toBeNull()
    expect(keyFromHash('#key=')).toBeNull()
    expect(keyFromHash('#other=1')).toBeNull()
  })
})

describe('callDuration', () => {
  it('counts minutes and seconds', () => {
    expect(callDuration(0)).toBe('0:00')
    expect(callDuration(7_900)).toBe('0:07')
    expect(callDuration(750_000)).toBe('12:30')
  })

  it('never goes below zero', () => {
    expect(callDuration(-500)).toBe('0:00')
  })
})

describe('initials', () => {
  it('takes the first letters of the first two words', () => {
    expect(initials('Maple Lane Burgers')).toBe('ML')
    expect(initials('frontdesk')).toBe('F')
    expect(initials('  ')).toBe('?')
  })
})

describe('statusLine', () => {
  it('follows the call from tap to hang-up', () => {
    expect(statusLine({ ...IDLE }, null)).toBe('Tap to call')
    expect(statusLine({ ...IDLE, status: 'connecting' }, null)).toBe('Calling…')
    expect(statusLine({ ...IDLE, status: 'live' }, 42_000)).toBe('0:42')
    expect(statusLine({ ...IDLE, status: 'ended' }, 95_000)).toBe('Call ended · 1:35')
    expect(statusLine({ ...IDLE, status: 'ended' }, null)).toBe('Call ended')
  })

  it('says what went wrong when the call failed', () => {
    expect(statusLine({ status: 'failed', problem: 'The call dropped.' }, null)).toBe('The call dropped.')
  })
})

describe('speakingLine', () => {
  it('says who is talking, only while the call is live', () => {
    const live = { ...IDLE, status: 'live' as const }
    expect(speakingLine({ ...live, agentSpeaking: true })).toBe('Frontdesk is speaking')
    expect(speakingLine({ ...live, customerSpeaking: true })).toBe('Listening…')
    expect(speakingLine(live)).toBeNull()
    expect(speakingLine({ ...IDLE, agentSpeaking: true })).toBeNull()
  })
})

describe('turnedAwayLine', () => {
  it('explains a call that was not put through', () => {
    expect(turnedAwayLine('blocked')).toMatch(/blocked/)
    expect(turnedAwayLine('too_many_calls')).toMatch(/Too many calls/)
    expect(turnedAwayLine(null)).toBeNull()
  })
})

describe('describeLineProblem', () => {
  it('turns the backend codes into words for the caller', () => {
    expect(describeLineProblem(new ApiError(403, 'wrong_web_phone_key', 'x'))).toMatch(/Ask for a new link/)
    expect(describeLineProblem(new ApiError(503, 'web_phone_off', 'x'))).toMatch(/switched off/)
    expect(describeLineProblem(new ApiError(409, 'voice_call_in_progress', 'x'))).toMatch(/busy/)
    expect(describeLineProblem(new ApiError(503, 'agent_unavailable', 'x'))).toMatch(/can't take calls/)
  })

  it("passes on the backend's own words for anything else, and covers no answer at all", () => {
    expect(describeLineProblem(new ApiError(422, 'not_an_offer', 'Bad offer.'))).toBe('Bad offer.')
    expect(describeLineProblem(new TypeError('Failed to fetch'))).toMatch(/could not be reached/)
  })
})
