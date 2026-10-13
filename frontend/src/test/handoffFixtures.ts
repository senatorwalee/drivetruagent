/**
 * Handoff packages as the backend really returns them, for tests of the
 * staff screen.
 */
import type { HandoffPackage } from '../api/client'
import { cart } from './fixtures'

const SESSION_ID = '11111111-1111-4111-8111-111111111111'

/** Two items in the cart, and the customer asked for 400 cookies on top. */
export const largeOrder: HandoffPackage = {
  handoff_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  session_id: SESSION_ID,
  lane: 'lane-1',
  language: 'en',
  status: 'open',
  reason: { code: 'extreme_quantity', title: 'Very large order' },
  created_at: '2026-10-13T12:03:00Z',
  acknowledged_by: null,
  item_count: 2,
  total_cents: 698,
  details: { line_quantity: 400, cart_item_count: 402 },
  outstanding:
    'The customer asked for 400 of Chocolate chip cookie and confirmed it. That is more than the agent may take, so it was not added. Check the quantity with them. The cart holds 2 items and can be placed as it stands.',
  acknowledged_at: null,
  resolved_at: null,
  outcome: null,
  note: null,
  session_started_at: '2026-10-13T12:00:00Z',
  session_ended_at: '2026-10-13T12:03:00Z',
  cart: { ...cart, state: 'handed_off' },
  stock: [
    { item_code: 'cola', item_name: 'Cola', quantity: 1, status: 'active', expires_at: '2026-10-13T12:13:00Z', held: true },
    { item_code: 'fries', item_name: 'Fries', quantity: 1, status: 'active', expires_at: '2026-10-13T12:13:00Z', held: true },
  ],
  transcript: [
    { speaker: 'customer', text: 'Four hundred cookies.', at: '2026-10-13T12:02:40Z' },
    { speaker: 'agent', text: 'Four hundred cookies, is that right?', at: '2026-10-13T12:02:42Z' },
    { speaker: 'customer', text: 'Yes.', at: '2026-10-13T12:02:55Z' },
    { speaker: 'agent', text: 'A team member will be right with you.', at: '2026-10-13T12:03:01Z' },
  ],
  earlier_lines: 6,
  steps: [
    {
      at: '2026-10-13T12:02:41Z',
      tool: 'add_item',
      arguments: { item_code: 'cookie', quantity: 400, selections: [] },
      decision: 'confirm',
      rule: 'extreme_quantity',
      message: 'That would be 400 of one item.',
    },
    {
      at: '2026-10-13T12:03:00Z',
      tool: 'confirm_pending_action',
      arguments: { pending_action_id: 'p-1' },
      decision: 'escalate',
      rule: 'extreme_quantity',
      message: 'This order is too large to take here.',
    },
  ],
  earlier_steps: 0,
  order: null,
}

/** Nothing ordered yet when the three-minute limit arrived. */
export const timedOut: HandoffPackage = {
  ...largeOrder,
  handoff_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  session_id: '33333333-3333-4333-8333-333333333333',
  lane: 'lane-2',
  reason: { code: 'time_limit', title: 'Took longer than the time limit' },
  created_at: '2026-10-13T12:04:00Z',
  item_count: 0,
  total_cents: 0,
  details: {},
  outstanding: 'The order was not finished within the time limit. Finish it with the customer. The cart is empty.',
  cart: { ...cart, state: 'handed_off', lines: [], item_count: 0, subtotal_cents: 0, tax_cents: 0, total_cents: 0 },
  stock: [],
  transcript: [{ speaker: 'agent', text: 'A team member will help you finish your order. One moment, please.', at: '2026-10-13T12:04:00Z' }],
  earlier_lines: 0,
  steps: [{ at: '2026-10-13T12:04:00Z', tool: 'timer', arguments: { after_seconds: 180 }, decision: null, rule: 'time_limit', message: 'hand off' }],
}

/** One that was cancelled earlier. */
export const cancelledEarlier: HandoffPackage = {
  ...timedOut,
  handoff_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  session_id: '44444444-4444-4444-8444-444444444444',
  lane: 'lane-3',
  status: 'resolved',
  created_at: '2026-10-13T11:00:00Z',
  resolved_at: '2026-10-13T11:02:00Z',
  outcome: 'cancelled',
  note: 'Customer drove off.',
}
