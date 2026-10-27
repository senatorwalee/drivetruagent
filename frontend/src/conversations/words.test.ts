import { describe, expect, it } from 'vitest'

import type { ConversationSummary, DaySpend, SessionFacts } from '../api/client'
import { authorNote, dayLine, duration, factLines, noteMeta, outcomeAsked, outcomeLabel, placeLabel, shown, whyNotTheModel } from './words'

const FACTS: SessionFacts = {
  outcome: 'submitted',
  end_reason: null,
  channel: 'drive_thru',
  language: 'en',
  started_at: '2026-10-27T18:00:00Z',
  ended_at: '2026-10-27T18:01:35Z',
  duration_seconds: 95,
  customer_turns: 3,
  order: { order_number: 12, lines: [{ quantity: 2, item_name: 'Cheeseburger' }], total_cents: 1354 },
  handoff: null,
  abuse_strikes: 0,
  confusion_strikes: 0,
  flagged_messages: 0,
  unmet_requests: [],
  refused: [],
  nudges: 0,
  model_calls: 4,
  input_tokens: 12_345,
  output_tokens: 210,
}

const note = (overrides: Partial<ConversationSummary>): ConversationSummary => ({
  session_id: 's1',
  lane: 'lane-1',
  channel: 'drive_thru',
  language: 'en',
  outcome: 'submitted',
  started_at: FACTS.started_at,
  ended_at: FACTS.ended_at,
  gist: 'Ordered two cheeseburgers.',
  follow_up: null,
  mood: 'pleased',
  written_by: 'model',
  order_number: 12,
  total_cents: 1354,
  handoff_title: null,
  written_at: FACTS.ended_at,
  cost: null,
  ...overrides,
})

describe('filters', () => {
  it('asks the backend for one outcome, and filters follow-ups here', () => {
    expect(outcomeAsked('all')).toBeUndefined()
    expect(outcomeAsked('follow_up')).toBeUndefined()
    expect(outcomeAsked('handed_off')).toBe('handed_off')

    const notes = [note({ session_id: 'a' }), note({ session_id: 'b', follow_up: 'Call back about the allergy.' })]
    expect(shown(notes, 'follow_up').map((n) => n.session_id)).toEqual(['b'])
    expect(shown(notes, 'all')).toHaveLength(2)
  })
})

describe('a note’s words', () => {
  it('names outcomes and places plainly', () => {
    expect(outcomeLabel('submitted')).toBe('ordered')
    expect(outcomeLabel('abandoned')).toBe('left')
    expect(placeLabel({ channel: 'phone', lane: 'phone-abc' })).toBe('Phone')
    expect(placeLabel({ channel: 'drive_thru', lane: 'lane-2' })).toBe('Drive-thru · lane-2')
  })

  it('gives lengths in minutes and seconds', () => {
    expect(duration(45)).toBe('45 s')
    expect(duration(125)).toBe('2 min 05 s')
  })

  it('says who wrote it', () => {
    expect(authorNote({ written_by: 'model', mood: 'frustrated' })).toBe('written by the model · frustrated')
    expect(authorNote({ written_by: 'template', mood: null })).toBe('written from the log')
  })

  it('explains why the model’s words were not kept', () => {
    expect(whyNotTheModel(null)).toBeNull()
    expect(whyNotTheModel('no_model')).toMatch(/No model is set up/)
    expect(whyNotTheModel('item_not_in_conversation: Fish burger')).toBe(
      'The model’s note named Fish burger, which was never said, so it was not kept.',
    )
    expect(whyNotTheModel('wrong_order_number: 13')).toMatch(/order #13/)
    expect(whyNotTheModel('something_new')).toBe('The model’s note was not kept (something_new).')
  })
})

describe('the facts', () => {
  it('lists what happened and leaves out what did not', () => {
    expect(factLines(FACTS)).toEqual([
      { label: 'Length', value: '1 min 35 s, 3 things said by the customer' },
      { label: 'Language', value: 'English' },
      { label: 'Model', value: '4 calls · 12,345 tokens in, 210 out' },
    ])
  })

  it('includes handoffs, unmet requests, refusals, guardrail flags and nudges', () => {
    const labels = factLines({
      ...FACTS,
      handoff: { reason: 'human_requested', title: 'A person was asked for' },
      unmet_requests: ['oat milk shake'],
      refused: ['extreme_quantity'],
      flagged_messages: 1,
      abuse_strikes: 1,
      nudges: 2,
    })

    expect(labels).toContainEqual({ label: 'Handed off', value: 'A person was asked for' })
    expect(labels).toContainEqual({ label: 'Asked for, not on the menu', value: 'oat milk shake' })
    expect(labels).toContainEqual({ label: 'Rules that said no', value: 'extreme quantity' })
    expect(labels).toContainEqual({ label: 'Guardrail', value: '1 message flagged · 1 abuse, 0 confusion strikes' })
    expect(labels).toContainEqual({ label: 'Asked if still there', value: '2 times' })
  })
})

describe('costs', () => {
  const cost = (micro_usd: number) => ({ micro_usd, calls: 4, unpriced_calls: 0, estimated_calls: 0 })

  it('puts what the conversation cost under its note, when known', () => {
    expect(noteMeta(note({ cost: cost(2_689) }))).toBe('written by the model · pleased · model cost 0.27¢')
    expect(noteMeta(note({ cost: null }))).toBe('written by the model · pleased')
  })

  it('adds the cost to the model line of the facts', () => {
    const model = factLines({ ...FACTS, cost: cost(2_689) }).find((fact) => fact.label === 'Model')
    expect(model?.value).toBe('4 calls · 12,345 tokens in, 210 out · 0.27¢')
  })

  it('sums up the day', () => {
    const day: DaySpend = {
      business_date: '2026-10-27',
      conversations: 14,
      orders: 5,
      total: cost(58_000),
      by_purpose: {},
      by_channel: {},
      per_conversation_micro_usd: 4_143,
      per_order_micro_usd: 11_600,
      prices_checked_on: '2026-10-27',
      prices_source: 'https://example.test',
    }
    expect(dayLine(day)).toBe('14 conversations · 5 orders · 0.41¢ a conversation · 1.2¢ an order · 5.8¢ in all')
    expect(dayLine({ ...day, conversations: 0, orders: 0, per_conversation_micro_usd: null, per_order_micro_usd: null })).toBe(
      '0 conversations · 0 orders · — a conversation · — an order · 5.8¢ in all',
    )
  })
})
