import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import { IDLE } from '../voice/voiceCall'
import {
  callDuration,
  chooseLine,
  describeLineProblem,
  initials,
  keyFromHash,
  lineFromHash,
  lineLabel,
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

describe('lineFromHash', () => {
  it('reads the line after the #', () => {
    expect(lineFromHash('#key=abc&line=2')).toBe(2)
  })

  it('is null with no line, or one that is not a line number', () => {
    expect(lineFromHash('#key=abc')).toBeNull()
    expect(lineFromHash('#line=0')).toBeNull()
    expect(lineFromHash('#line=two')).toBeNull()
    expect(lineFromHash('#line=1.5')).toBeNull()
  })
})

describe('chooseLine', () => {
  const lines = [
    { line: 1, caller_last_four: '0123', busy: true },
    { line: 2, caller_last_four: '0188', busy: false },
    { line: 3, caller_last_four: '0142', busy: false },
  ]

  it('takes the pick, then the link, then the first free line', () => {
    expect(chooseLine(lines, 3, 1)).toBe(3)
    expect(chooseLine(lines, null, 1)).toBe(1)
    expect(chooseLine(lines, null, null)).toBe(2)
  })

  it('passes over a line that is not there', () => {
    expect(chooseLine(lines, 9, 7)).toBe(2)
  })

  it('is line 1 before the lines are known, or when all are busy', () => {
    expect(chooseLine([], null, null)).toBe(1)
    expect(chooseLine([{ ...lines[0]! }], null, null)).toBe(1)
  })
})

describe('lineLabel', () => {
  it('shows the last four digits, or that the number is withheld', () => {
    expect(lineLabel({ caller_last_four: '0123' })).toBe('…0123')
    expect(lineLabel({ caller_last_four: null })).toBe('Withheld')
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
    expect(describeLineProblem(new ApiError(409, 'web_phone_line_busy', 'x'))).toMatch(/busy/)
    expect(describeLineProblem(new ApiError(404, 'no_such_web_phone_line', 'x'))).toMatch(/isn't set up/)
    expect(describeLineProblem(new ApiError(503, 'agent_unavailable', 'x'))).toMatch(/can't take calls/)
  })

  it("passes on the backend's own words for anything else, and covers no answer at all", () => {
    expect(describeLineProblem(new ApiError(422, 'not_an_offer', 'Bad offer.'))).toBe('Bad offer.')
    expect(describeLineProblem(new TypeError('Failed to fetch'))).toMatch(/could not be reached/)
  })
})
