/**
 * Turns a session's raw event log into what the screen shows.
 *
 * Pure functions, no React and no network. The event log is the backend's
 * audit trail: every message, tool call and tool result, in order. Everything
 * here is derived from it, so the screen never keeps its own copy of the
 * conversation that could drift from the truth.
 */
import type { AgentEvent } from '../api/client'
import { clock } from './presentation'
import type { Decision, DecisionKind, PendingItem, PlacedOrder, Timeline, TranscriptLine } from './types'

type Json = Record<string, unknown>

interface ToolCallPayload {
  call_id: string
  name: string
  arguments: Json
}

interface ToolResultPayload {
  call_id: string
  name: string
  result: Json
}

/** Tools that change the cart, and so can leave something waiting for a yes. */
const HOLDABLE_TOOLS = new Set(['add_item', 'update_item'])
const DECISIONS = new Set<DecisionKind>(['allow', 'reject', 'confirm', 'escalate'])

export function buildTimeline(events: readonly AgentEvent[]): Timeline {
  const transcript: TranscriptLine[] = []
  const decisions: Decision[] = []
  const calls = new Map<string, ToolCallPayload>()
  let pending: PendingItem | null = null
  let order: PlacedOrder | null = null

  for (const event of events) {
    switch (event.kind) {
      case 'user_message':
        transcript.push({ id: event.seq, speaker: 'customer', text: textOf(event) })
        break
      case 'assistant_message':
        transcript.push({ id: event.seq, speaker: 'agent', text: textOf(event) })
        break
      case 'tool_call': {
        const call = event.payload as unknown as ToolCallPayload
        calls.set(call.call_id, call)
        break
      }
      case 'tool_result': {
        const { call_id, name, result } = event.payload as unknown as ToolResultPayload
        const args = calls.get(call_id)?.arguments ?? {}
        const kind = decisionKind(result)
        decisions.push({
          id: event.seq,
          tool: name,
          summary: summarise(args),
          kind,
          reason: reasonOf(result),
          suggestion: suggestionOf(result),
          cartVersion: cartVersionOf(result),
        })
        pending = nextPending(pending, name, kind, args)
        order = placedOrder(result) ?? order
        break
      }
      case 'timer': {
        const decision = timerDecision(event)
        decisions.push(decision)
        // Anything but a nudge ended the session, so nothing is waiting for
        // a yes any more.
        if (event.payload['action'] !== 'nudge') pending = null
        break
      }
      case 'guardrail': {
        const decision = guardrailDecision(event)
        decisions.push(decision)
        if (decision.kind === 'escalate') pending = null
        break
      }
      default:
        // model_call events are bookkeeping; the screen does not show them.
        break
    }
  }

  return { transcript, decisions, pending, order }
}

const TIMER_WORDS: Record<string, string> = { nudge: 'asked', abandon: 'closed' }

/**
 * A timer event as an entry in the decision log. These are the backend acting
 * by itself: asking a silent customer if they are there, closing a session
 * nobody answered in, or handing a long one to a person.
 */
function timerDecision(event: AgentEvent): Decision {
  const action = event.payload['action']
  const seconds = typeof event.payload['after_seconds'] === 'number' ? event.payload['after_seconds'] : 0
  const rule = event.payload['rule']
  const handOff = action === 'hand_off'
  return {
    id: event.seq,
    tool: 'timer',
    summary: handOff ? `handed off at ${clock(seconds)}` : `${TIMER_WORDS[String(action)] ?? action} after ${seconds}s of silence`,
    // Handing off is the same outcome as any other escalation, so it is
    // shown the same way, with the rule that caused it.
    kind: handOff ? 'escalate' : 'timer',
    reason: handOff && typeof rule === 'string' ? rule : null,
    suggestion: null,
    cartVersion: null,
  }
}

const VERDICT_WORDS: Record<string, string> = { off_topic: 'off topic', abuse: 'abuse', extraction: 'extraction' }
/** What ended the session, as the rule code staff see elsewhere. */
const ENDING_RULES: Record<string, string> = { terminate: 'abuse', hand_off: 'repeated_confusion' }

/**
 * A guardrail event as an entry in the decision log: a message the backend
 * flagged before the agent saw it, or a turn the agent got nowhere with.
 * A strike is shown as a rejection; ending the session or handing it to a
 * person as an escalation, with the rule behind it.
 */
function guardrailDecision(event: AgentEvent): Decision {
  const { verdict, reason, source, action } = event.payload
  const offTopic = verdict === 'off_topic'
  const strikes = event.payload[offTopic ? 'confusion_strikes' : 'abuse_strikes']
  const limit = event.payload['strike_limit']
  const what = source === 'round_limit' ? 'agent got nowhere' : (VERDICT_WORDS[String(verdict)] ?? String(verdict))
  const strike = typeof strikes === 'number' && typeof limit === 'number' ? `strike ${strikes} of ${limit}` : 'strike'
  const ending = ENDING_RULES[String(action)]
  return {
    id: event.seq,
    tool: 'guardrail',
    summary: `${what} · ${strike}`,
    kind: ending ? 'escalate' : 'reject',
    // A rule's name says what matched; a classifier's reason is a sentence
    // from a model and is left to the event log.
    reason: ending ?? (source === 'rules' && typeof reason === 'string' ? reason : null),
    suggestion: null,
    cartVersion: null,
  }
}

function textOf(event: AgentEvent): string {
  const text = event.payload['text']
  return typeof text === 'string' ? text : ''
}

function decisionKind(result: Json): DecisionKind {
  const decision = result['decision']
  if (typeof decision === 'string' && DECISIONS.has(decision as DecisionKind)) {
    return decision as DecisionKind
  }
  // No decision field: either a lookup, or a tool that failed before policy ran.
  return 'error' in result ? 'reject' : 'read'
}

function reasonOf(result: Json): string | null {
  if (typeof result['rule'] === 'string') return result['rule']
  const error = result['error'] as { code?: unknown } | undefined
  return typeof error?.code === 'string' ? error.code : null
}

function suggestionOf(result: Json): string | null {
  const suggestion = result['suggestion'] as { name?: unknown } | undefined
  return typeof suggestion?.name === 'string' ? suggestion.name : null
}

function cartVersionOf(result: Json): number | null {
  const cart = result['cart'] as { version?: unknown } | undefined
  return typeof cart?.version === 'number' ? cart.version : null
}

function placedOrder(result: Json): PlacedOrder | null {
  const order = result['order'] as { order_number?: unknown; total_cents?: unknown } | undefined
  if (typeof order?.order_number !== 'number' || typeof order.total_cents !== 'number') return null
  return { orderNumber: order.order_number, totalCents: order.total_cents }
}

/**
 * What is waiting for the customer's yes after this tool result.
 *
 * A `confirm` on a cart change starts a wait. Anything that runs afterwards
 * ends it: the backend drops a held action as soon as it is answered or the
 * cart moves on, and this mirrors that.
 */
function nextPending(current: PendingItem | null, tool: string, kind: DecisionKind, args: Json): PendingItem | null {
  if (kind === 'confirm' && HOLDABLE_TOOLS.has(tool)) {
    const quantity = args['quantity']
    const itemCode = args['item_code']
    if (typeof quantity === 'number' && typeof itemCode === 'string') {
      return { quantity, itemCode }
    }
    return null
  }
  // Lookups and rejections change nothing, so a wait survives them.
  return kind === 'read' || kind === 'reject' ? current : null
}

/** The arguments of a tool call in a short readable form. */
export function summarise(args: Json): string {
  const parts: string[] = []
  const quantity = typeof args['quantity'] === 'number' ? args['quantity'] : null

  if (typeof args['line_no'] === 'number') parts.push(`L${args['line_no']}`)
  if (typeof args['item_code'] === 'string') {
    parts.push(quantity !== null && quantity !== 1 ? `${args['item_code']} × ${quantity}` : args['item_code'])
  } else if (quantity !== null) {
    parts.push(`quantity=${quantity}`)
  }
  parts.push(...selectionParts(args['selections']))
  if (typeof args['query'] === 'string') parts.push(`"${args['query']}"`)
  if (Array.isArray(args['allergens']) && args['allergens'].length > 0) parts.push(args['allergens'].join('+'))
  if (typeof args['language'] === 'string') parts.push(args['language'])
  if (typeof args['reason'] === 'string') parts.push(args['reason'])

  return parts.join(' · ')
}

function selectionParts(selections: unknown): string[] {
  if (!Array.isArray(selections)) return []
  return selections.flatMap((selection: { group?: unknown; options?: unknown }) => {
    if (typeof selection.group !== 'string' || !Array.isArray(selection.options)) return []
    const options = selection.options.length > 0 ? selection.options.join('+') : 'none'
    return [`${selection.group}=${options}`]
  })
}
