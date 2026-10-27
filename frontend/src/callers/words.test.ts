import { describe, expect, it } from 'vitest'

import type { Caller } from '../api/client'
import { maskedPhone, pickupLines, pickupStatusLabel, spacedPhone, standingReason, statusActions } from './words'

const NOW = Date.parse('2026-10-27T18:00:00Z')

describe('phone numbers', () => {
  it('hides all but the last four digits', () => {
    expect(maskedPhone('+14165550123')).toBe('••• ••• 0123')
  })

  it('spaces a North American number the way it is read out', () => {
    expect(spacedPhone('+14165550123')).toBe('+1 416 555 0123')
    expect(spacedPhone('+442071838750')).toBe('+442071838750')
  })
})

describe('pickups', () => {
  it('lists what was ordered', () => {
    const lines = [
      { quantity: 2, item_name: 'Cheeseburger' },
      { quantity: 1, item_name: 'Fries' },
    ]
    expect(pickupLines({ lines })).toBe('2 × Cheeseburger, 1 × Fries')
  })

  it('says who marked a pickup and when', () => {
    expect(pickupStatusLabel({ status: 'waiting', marked_by: null, marked_at: null }, NOW)).toBe('waiting')
    expect(pickupStatusLabel({ status: 'no_show', marked_by: 'Sam', marked_at: '2026-10-27T17:55:00Z' }, NOW)).toBe(
      'no-show · Sam, 5 min ago',
    )
    expect(pickupStatusLabel({ status: 'picked_up', marked_by: 'Alex', marked_at: '2026-10-27T17:59:50Z' }, NOW)).toBe(
      'picked up · Alex, just now',
    )
  })
})

type Standingish = Pick<Caller, 'standing' | 'status' | 'recent_no_shows' | 'status_reason' | 'status_changed_by'>
const caller = (overrides: Partial<Standingish>): Standingish => ({
  standing: 'normal',
  status: 'normal',
  recent_no_shows: 0,
  status_reason: null,
  status_changed_by: null,
  ...overrides,
})

describe('why a number stands where it does', () => {
  it('says nothing about a number with nothing against it', () => {
    expect(standingReason(caller({}))).toBe('')
  })

  it('counts the no-shows that flagged a number', () => {
    expect(standingReason(caller({ standing: 'flagged', recent_no_shows: 2 }))).toBe('2 recent no-shows')
  })

  it('quotes who decided and why', () => {
    expect(
      standingReason(caller({ standing: 'blocked', status: 'blocked', status_reason: 'Prank calls', status_changed_by: 'Manager' })),
    ).toBe('Blocked by Manager: Prank calls')
    expect(
      standingReason(caller({ standing: 'flagged', status: 'flagged', status_reason: 'Fake order', status_changed_by: 'Sam' })),
    ).toBe('Flagged by Sam: Fake order')
  })

  it('still mentions no-shows on a number staff cleared', () => {
    expect(
      standingReason(caller({ standing: 'flagged', recent_no_shows: 1, status_reason: 'Paid up', status_changed_by: 'Sam' })),
    ).toBe('1 recent no-show')
  })
})

describe('what staff can change a number to', () => {
  it('offers the next steps from each status', () => {
    expect(statusActions('normal').map((a) => a.label)).toEqual(['Flag', 'Block'])
    expect(statusActions('flagged').map((a) => a.label)).toEqual(['Block', 'Clear flag'])
    expect(statusActions('blocked').map((a) => a.label)).toEqual(['Unblock'])
  })
})
