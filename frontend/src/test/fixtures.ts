/**
 * Realistic data for tests: the shapes the backend really returns, taken from
 * the scripted demo conversation.
 */
import type { AgentEvent, AgentInfo, Cart, Session } from '../api/client'
import type { LiveSession } from '../session/useLiveSession'
import { buildTimeline } from '../session/timeline'

const SESSION_ID = '11111111-1111-4111-8111-111111111111'
const TURN_ID = '22222222-2222-4222-8222-222222222222'

let nextSeq = 1

export function resetSeq(): void {
  nextSeq = 1
}

function event(kind: AgentEvent['kind'], payload: Record<string, unknown>): AgentEvent {
  return { seq: nextSeq++, turn_id: TURN_ID, kind, payload, created_at: '2026-10-13T12:00:00Z' }
}

/** The backend acting on a clock: 'nudge', 'abandon' or 'hand_off'. */
export const timer = (action: string, rule: string, after_seconds: number) => event('timer', { action, rule, after_seconds })
export const said = (text: string) => event('user_message', { text })
export const replied = (text: string) => event('assistant_message', { text })
export const modelCall = () => event('model_call', { model: 'demo-script', latency_ms: 3, tool_calls: 1 })

/** A tool call and its result, as the two events the backend records. */
export function toolRound(name: string, args: Record<string, unknown>, result: Record<string, unknown>): AgentEvent[] {
  const call_id = `call_${nextSeq}`
  return [event('tool_call', { call_id, name, arguments: args }), event('tool_result', { call_id, name, result })]
}

export const size = (option: string) => [{ group: 'size', options: [option] }]

export const agentInfo: AgentInfo = {
  restaurant_name: 'Maple Lane Burgers',
  available: true,
  model: 'demo-script',
  scripted: true,
  suggested_lines: ['Hi', 'Can I get some fries?', 'Large, and a medium cola.'],
}

export const session: Session = {
  session_id: SESSION_ID,
  lane: 'screen-test',
  state: 'ordering',
  language: null,
  started_at: '2026-10-13T12:00:00Z',
  ended_at: null,
  end_reason: null,
  business_date: '2026-10-13',
  timers: {
    paused: false,
    paused_at: null,
    paused_seconds: 0,
    nudge_at: '2026-10-13T12:01:02Z',
    closes_at: null,
    hand_off_at: '2026-10-13T12:03:00Z',
  },
}

/** One large fries and one medium cola. */
export const cart: Cart = {
  session_id: SESSION_ID,
  state: 'ordering',
  language: 'en',
  version: 2,
  item_count: 2,
  subtotal_cents: 618,
  discounts: [],
  discount_cents: 0,
  tax_cents: 80,
  total_cents: 698,
  lines: [
    {
      line_no: 1,
      item_code: 'fries',
      item_name: 'Fries',
      quantity: 1,
      unit_price_cents: 379,
      line_total_cents: 379,
      options: [
        { group_code: 'size', group_name: 'Size', option_code: 'large', option_name: 'Large', price_delta_cents: 130 },
        { group_code: 'style', group_name: 'Style', option_code: 'regular', option_name: 'Regular', price_delta_cents: 0 },
      ],
    },
    {
      line_no: 2,
      item_code: 'cola',
      item_name: 'Cola',
      quantity: 1,
      unit_price_cents: 239,
      line_total_cents: 239,
      options: [
        { group_code: 'size', group_name: 'Size', option_code: 'medium', option_name: 'Medium', price_delta_cents: 40 },
      ],
    },
  ],
}

/** The same cart after a cheeseburger made it a combo: 200 off, taxed on the rest. */
export const comboCart: Cart = {
  ...cart,
  version: 3,
  item_count: 3,
  subtotal_cents: 1217,
  discounts: [{ kind: 'combo', code: 'burger_combo', name: 'Burger combo', times: 1, amount_cents: 200 }],
  discount_cents: 200,
  tax_cents: 132,
  total_cents: 1149,
  lines: [
    ...cart.lines,
    { line_no: 3, item_code: 'cheeseburger', item_name: 'Cheeseburger', quantity: 1, unit_price_cents: 599, line_total_cents: 599, options: [] },
  ],
}

/**
 * The demo conversation up to "Fifteen cookies, is that right?": a refusal,
 * two items added, and a quantity held for confirmation.
 */
export function eventsUpToHeldCookies(): AgentEvent[] {
  resetSeq()
  return [
    said('Can I get some fries?'),
    ...toolRound(
      'add_item',
      { item_code: 'fries', quantity: 1, selections: [] },
      { decision: 'reject', rule: 'missing_required_options', error: { code: 'missing_required_options' }, cart: { version: 0 } },
    ),
    replied('Sure. What size would you like?'),
    said('Large, and a medium cola.'),
    ...toolRound('add_item', { item_code: 'fries', quantity: 1, selections: size('large') }, { decision: 'allow', cart: { version: 1 } }),
    ...toolRound('add_item', { item_code: 'cola', quantity: 1, selections: size('medium') }, { decision: 'allow', cart: { version: 2 } }),
    modelCall(),
    replied('Large fries and a medium cola. Anything else?'),
    said('Fifteen cookies.'),
    ...toolRound(
      'add_item',
      { item_code: 'cookie', quantity: 15, selections: [] },
      { decision: 'confirm', rule: 'unusual_quantity', pending_action_id: 'p-1', cart: { version: 2 } },
    ),
    replied('Fifteen cookies, is that right?'),
  ]
}

const ITEM_NAMES: Record<string, string> = { cookie: 'Chocolate chip cookie', fries: 'Fries', cola: 'Cola' }

/** A complete LiveSession for rendering a view, mid-order by default. */
export function liveSession(overrides: Partial<LiveSession> = {}): LiveSession {
  return {
    status: 'ready',
    problem: null,
    agent: agentInfo,
    session,
    cart,
    timeline: buildTimeline(eventsUpToHeldCookies()),
    agentState: 'speaking',
    elapsedSeconds: 42,
    timerNote: 'hand-off in 2:18',
    suggestedLine: 'No, just two.',
    itemName: (code) => ITEM_NAMES[code] ?? code,
    paused: false,
    canSend: true,
    send: () => {},
    startNewSession: () => {},
    togglePause: () => {},
    setCustomerActive: () => {},
    ...overrides,
  }
}
