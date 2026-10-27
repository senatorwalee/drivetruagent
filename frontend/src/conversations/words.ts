/**
 * Pure helpers that turn session summaries into the words the conversations
 * page shows. No React and no network, so each is tested on its own.
 */
import type { ConversationSummary, DaySpend, Mood, SessionFacts, SessionState } from '../api/client'
import { modelCost } from '../session/presentation'

/** What the page can show: everything, the notes with something to do, or one outcome. */
export type Filter = 'all' | 'follow_up' | SessionState

export const FILTERS: { filter: Filter; label: string }[] = [
  { filter: 'all', label: 'All' },
  { filter: 'follow_up', label: 'To follow up' },
  { filter: 'submitted', label: 'Ordered' },
  { filter: 'handed_off', label: 'Handed off' },
  { filter: 'abandoned', label: 'Left' },
  { filter: 'terminated', label: 'Ended' },
]

/** The outcome to ask the backend for; the follow-up filter is applied here. */
export function outcomeAsked(filter: Filter): SessionState | undefined {
  return filter === 'all' || filter === 'follow_up' ? undefined : filter
}

export function shown(summaries: ConversationSummary[], filter: Filter): ConversationSummary[] {
  return filter === 'follow_up' ? summaries.filter((summary) => summary.follow_up) : summaries
}

const OUTCOMES: Partial<Record<SessionState, string>> = {
  submitted: 'ordered',
  handed_off: 'handed off',
  abandoned: 'left',
  terminated: 'ended for misuse',
}

export function outcomeLabel(outcome: SessionState): string {
  return OUTCOMES[outcome] ?? outcome.replaceAll('_', ' ')
}

/** Where the customer was: "Phone", or "Drive-thru · lane-1". */
export function placeLabel(summary: Pick<ConversationSummary, 'channel' | 'lane'>): string {
  return summary.channel === 'phone' ? 'Phone' : `Drive-thru · ${summary.lane}`
}

/** "45 s", "2 min 05 s". */
export function duration(seconds: number): string {
  if (seconds < 60) return `${seconds} s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes} min ${String(seconds % 60).padStart(2, '0')} s`
}

const MOODS: Record<Mood, string> = {
  pleased: 'pleased',
  neutral: 'neutral',
  frustrated: 'frustrated',
}

/** Who wrote the words, and the mood if the model judged one. */
export function authorNote(summary: Pick<ConversationSummary, 'written_by' | 'mood'>): string {
  if (summary.written_by === 'template') return 'written from the log'
  return summary.mood ? `written by the model · ${MOODS[summary.mood]}` : 'written by the model'
}

/** Why the model's words were not used, as a sentence. Null when they were, or nothing was asked. */
export function whyNotTheModel(reason: string | null): string | null {
  if (!reason) return null
  const [code, detail] = reason.split(': ', 2)
  switch (code) {
    case 'no_model':
      return 'No model is set up, so the note was written from the log.'
    case 'model_failed':
      return 'The model failed, so the note was written from the log.'
    case 'model_timed_out':
      return 'The model took too long, so the note was written from the log.'
    case 'no_summary_in_reply':
      return 'The model gave no note, so it was written from the log.'
    case 'too_long':
      return 'The model’s note ran too long, so it was not kept.'
    case 'phone_number':
      return 'The model’s note held a phone number, so it was not kept.'
    case 'item_not_in_conversation':
      return `The model’s note named ${detail ?? 'an item'}, which was never said, so it was not kept.`
    case 'amount_not_in_conversation':
      return `The model’s note gave ${detail ?? 'an amount'}, which was never said, so it was not kept.`
    case 'wrong_order_number':
      return `The model’s note gave order #${detail ?? '?'}, which is not this session’s, so it was not kept.`
    default:
      return `The model’s note was not kept (${reason}).`
  }
}

/** A rule's code as words: "extreme_quantity" → "extreme quantity". */
export function ruleWords(rule: string): string {
  return rule.replaceAll('_', ' ')
}

export interface Fact {
  label: string
  value: string
}

/** The facts worth a line each, leaving out what is zero or empty. */
export function factLines(facts: SessionFacts): Fact[] {
  const lines: Fact[] = [
    { label: 'Length', value: `${duration(facts.duration_seconds)}, ${counted(facts.customer_turns, 'thing')} said by the customer` },
  ]
  if (facts.language) lines.push({ label: 'Language', value: facts.language === 'fr' ? 'French' : 'English' })
  if (facts.handoff) lines.push({ label: 'Handed off', value: facts.handoff.title })
  if (facts.unmet_requests.length) lines.push({ label: 'Asked for, not on the menu', value: facts.unmet_requests.join(', ') })
  if (facts.refused.length) lines.push({ label: 'Rules that said no', value: facts.refused.map(ruleWords).join(', ') })
  if (facts.flagged_messages)
    lines.push({
      label: 'Guardrail',
      value: `${counted(facts.flagged_messages, 'message')} flagged · ${facts.abuse_strikes} abuse, ${facts.confusion_strikes} confusion strikes`,
    })
  if (facts.nudges) lines.push({ label: 'Asked if still there', value: counted(facts.nudges, 'time') })
  if (facts.model_calls) {
    const cost = facts.cost ? ` · ${modelCost(facts.cost)}` : ''
    lines.push({
      label: 'Model',
      value: `${counted(facts.model_calls, 'call')} · ${facts.input_tokens.toLocaleString('en-CA')} tokens in, ${facts.output_tokens.toLocaleString('en-CA')} out${cost}`,
    })
  }
  return lines
}

/** Who wrote it, and what the conversation cost, for under a note. */
export function noteMeta(summary: Pick<ConversationSummary, 'written_by' | 'mood' | 'cost'>): string {
  return summary.cost ? `${authorNote(summary)} · model cost ${modelCost(summary.cost)}` : authorNote(summary)
}

/** The day so far: "14 conversations · 5 orders · 0.4¢ a conversation · 1.1¢ an order · 6.0¢ in all". */
export function dayLine(day: DaySpend): string {
  const per = (micro: number | null) => (micro === null ? '—' : modelCost({ micro_usd: micro, unpriced_calls: 0, estimated_calls: 0 }))
  return [
    counted(day.conversations, 'conversation'),
    counted(day.orders, 'order'),
    `${per(day.per_conversation_micro_usd)} a conversation`,
    `${per(day.per_order_micro_usd)} an order`,
    `${modelCost(day.total)} in all`,
  ].join(' · ')
}

function counted(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}
