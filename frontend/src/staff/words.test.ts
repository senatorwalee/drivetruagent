import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import { describeProblem, holdLabel, outcomeSentence, statusLabel, waitingFor } from './words'

describe('statusLabel', () => {
  it('says a new handoff is waiting', () => {
    expect(statusLabel({ status: 'open', acknowledged_by: null })).toBe('waiting')
  })

  it('names who has picked it up', () => {
    expect(statusLabel({ status: 'acknowledged', acknowledged_by: 'Sam' })).toBe('with Sam')
  })

  it('says a finished one is closed, whoever had it', () => {
    expect(statusLabel({ status: 'resolved', acknowledged_by: 'Sam' })).toBe('closed')
  })
})

describe('waitingFor', () => {
  const created_at = '2026-10-13T12:00:00Z'

  it('counts from when the session was handed off', () => {
    expect(waitingFor({ created_at }, Date.parse('2026-10-13T12:01:35Z'))).toBe('1:35')
  })

  it('never shows a negative wait if the clocks disagree', () => {
    expect(waitingFor({ created_at }, Date.parse('2026-10-13T11:59:58Z'))).toBe('0:00')
  })
})

describe('outcomeSentence', () => {
  it('gives the order number when the order was placed', () => {
    expect(outcomeSentence({ outcome: 'order_placed', order: { order_number: 12 } as never })).toBe('Order 12 was placed.')
  })

  it('says nothing was ordered when it was cancelled', () => {
    expect(outcomeSentence({ outcome: 'cancelled', order: null })).toBe('Cancelled. Nothing was ordered.')
  })

  it('is null while the handoff is open', () => {
    expect(outcomeSentence({ outcome: null, order: null })).toBeNull()
  })
})

describe('holdLabel', () => {
  const expires_at = '2026-10-13T12:13:00Z'

  it('says until when stock is held', () => {
    expect(holdLabel({ status: 'active', held: true, expires_at })).toMatch(/^held until \d/)
  })

  it('warns when a hold has run out, though nothing has released it yet', () => {
    expect(holdLabel({ status: 'active', held: false, expires_at })).toBe('hold ran out')
  })

  it('says when the stock was taken or given back', () => {
    expect(holdLabel({ status: 'committed', held: false, expires_at })).toBe('taken for the order')
    expect(holdLabel({ status: 'released', held: false, expires_at })).toBe('released')
  })
})

describe('describeProblem', () => {
  it('is null when nothing went wrong', () => {
    expect(describeProblem(null)).toBeNull()
  })

  it('explains the failures staff can act on', () => {
    expect(describeProblem(new ApiError(409, 'insufficient_stock', 'x'))).toMatch(/stock for this order has gone/)
    expect(describeProblem(new ApiError(409, 'empty_cart', 'x'))).toBe('There is nothing in the cart to place.')
    expect(describeProblem(new ApiError(409, 'handoff_resolved', 'x'))).toBe('Someone else has already closed this handoff.')
  })

  it("falls back to the backend's own message for anything else", () => {
    expect(describeProblem(new ApiError(422, 'something_new', 'The backend says no.'))).toBe('The backend says no.')
  })

  it('says to check the backend when the request never arrived', () => {
    expect(describeProblem(new TypeError('Failed to fetch'))).toMatch(/Check the backend is running/)
  })
})
