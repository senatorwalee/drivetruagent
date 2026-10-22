import { describe, expect, it } from 'vitest'

import { lastAsked, matchLabel, share, suggestion, timesLabel } from './words'

const NOW = Date.parse('2026-10-22T12:00:00Z')

describe('insights words', () => {
  it('names how the closest item relates to the request', () => {
    expect(matchLabel('close')).toBe('close match')
    expect(matchLabel('none')).toBe('not on the menu')
  })

  it.each([
    ['2026-10-22T11:59:40Z', 'just now'],
    ['2026-10-22T11:45:00Z', '15 min ago'],
    ['2026-10-22T09:00:00Z', '3 h ago'],
    ['2026-10-21T09:00:00Z', 'yesterday'],
    ['2026-10-18T12:00:00Z', '4 days ago'],
  ])('says %s was asked %s', (iso, words) => {
    expect(lastAsked(iso, NOW)).toBe(words)
  })

  it('sizes bars against the most-asked request, never to nothing', () => {
    expect(share(41, 41)).toBe(100)
    expect(share(27, 41)).toBe(66)
    expect(share(1, 400)).toBe(4)
    expect(share(0, 0)).toBe(0)
  })

  it('counts in words', () => {
    expect(timesLabel(1)).toBe('1 time')
    expect(timesLabel(41)).toBe('41 times')
  })

  it('suggests what to do about the most-asked request', () => {
    const close = { request: 'oat milk shake', times: 41, match: 'close', last_asked: '2026-10-22T11:00:00Z' }
    const none = { request: 'gluten-free bun', times: 27, match: 'none', last_asked: '2026-10-22T11:00:00Z' }

    expect(suggestion(close)).toBe(
      'Guests asked for "oat milk shake" 41 times. The menu has something close but not this. Consider offering it, or a line that says it isn\'t available.',
    )
    expect(suggestion(none)).toMatch(/^Guests asked for "gluten-free bun" 27 times\. Nothing on the menu matches/)
  })
})
