/**
 * Small pure helpers every view uses to turn state into words and numbers:
 * money, elapsed time, the agent indicator's state, and the progress steps.
 */
import type { Discount, Session, SessionState } from '../api/client'
import type { AgentState } from './types'

export function money(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

/**
 * A model cost, from micro-dollars: "0.27¢" under a cent, "1.8¢" under a
 * dollar, "$1.23" above. "≈" when part of it was estimated, and "+ 2
 * unpriced" when some calls had no price at all.
 */
export function modelCost(cost: { micro_usd: number; unpriced_calls: number; estimated_calls: number }): string {
  const cents = cost.micro_usd / 10_000
  const amount = cents >= 100 ? `$${(cents / 100).toFixed(2)}` : cents >= 1 ? `${cents.toFixed(1)}¢` : `${cents.toFixed(2)}¢`
  const about = cost.estimated_calls > 0 ? '≈' : ''
  const unpriced = cost.unpriced_calls > 0 ? ` + ${cost.unpriced_calls} unpriced` : ''
  return `${about}${amount}${unpriced}`
}

/** A line as it was said: one the customer talked over says so. */
export function spokenText(line: { text: string; cutOff?: boolean }): string {
  return line.cutOff ? `${line.text} … (cut off)` : line.text
}

/** Seconds as m:ss, the way a timer reads. */
export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

/** An amount without the currency sign, the way a till prints a column of them. */
export function plainAmount(cents: number): string {
  return (cents / 100).toFixed(2)
}

/** A saving's name, with how many times it applied when that is more than once. */
export function discountLabel(discount: Pick<Discount, 'name' | 'times'>): string {
  return discount.times > 1 ? `${discount.name} × ${discount.times}` : discount.name
}

/**
 * How long a session has actually been running: time since it started, less
 * any time it spent paused. It stops when the session ends or is paused.
 */
export function runningSeconds(session: Pick<Session, 'started_at' | 'ended_at' | 'timers'>, nowMs: number): number {
  const stoppedAt = session.ended_at ?? session.timers.paused_at
  const until = stoppedAt ? Date.parse(stoppedAt) : nowMs
  return Math.max(0, (until - Date.parse(session.started_at)) / 1000 - session.timers.paused_seconds)
}

/** Seconds from now until a time the backend gave, or null if it gave none. */
export function secondsUntil(when: string | null | undefined, nowMs: number): number | null {
  return when ? Math.max(0, (Date.parse(when) - nowMs) / 1000) : null
}

/**
 * What the backend's timers are counting down to, in a few words. The times
 * come from the backend; the screen only subtracts. Null when no timer is
 * running, which is the case once a session has ended.
 */
export function timerNote(timers: Session['timers'] | undefined, nowMs: number): string | null {
  if (!timers) return null
  if (timers.paused) return 'timers paused'
  const closesIn = secondsUntil(timers.closes_at, nowMs)
  if (closesIn !== null) return `closes in ${clock(Math.ceil(closesIn))}`
  const handOffIn = secondsUntil(timers.hand_off_at, nowMs)
  if (handOffIn !== null) return `hand-off in ${clock(Math.ceil(handOffIn))}`
  return null
}

export function itemsLabel(count: number): string {
  return count === 1 ? '1 item' : `${count} items`
}

export const AGENT_STATE_LABEL: Record<AgentState, string> = {
  ready: 'Ready',
  listening: 'Listening',
  working: 'Working on it',
  speaking: 'Speaking',
  paused: 'Paused',
  ended: 'Session ended',
}

interface AgentSignals {
  paused: boolean
  ended: boolean
  /** A message has been sent and the reply has not arrived. */
  waitingForReply: boolean
  /** A reply has just arrived and is being "said". */
  speaking: boolean
  /** The customer is in the middle of typing or talking. */
  customerActive: boolean
}

/**
 * Which state the indicator shows. The order of the checks is the priority:
 * a paused screen shows paused whatever else is true, and so on down.
 */
export function deriveAgentState(signals: AgentSignals): AgentState {
  if (signals.paused) return 'paused'
  if (signals.waitingForReply) return 'working'
  if (signals.speaking) return 'speaking'
  if (signals.ended) return 'ended'
  if (signals.customerActive) return 'listening'
  return 'ready'
}

/** How long to show "speaking" for a reply: roughly the time to say it aloud. */
export function speakingMilliseconds(reply: string): number {
  return Math.min(6000, Math.max(1500, 900 + reply.length * 45))
}

export interface ProgressStep {
  label: string
  status: 'done' | 'current' | 'upcoming'
}

const HAPPY_PATH: { state: SessionState; label: string }[] = [
  { state: 'greeting', label: 'Greeting' },
  { state: 'ordering', label: 'Ordering' },
  { state: 'confirming', label: 'Confirming' },
  { state: 'submitted', label: 'Submitted' },
]

/** Labels for the ways a session can end other than an order being placed. */
const OTHER_ENDINGS: Partial<Record<SessionState, string>> = {
  handed_off: 'Handed to staff',
  abandoned: 'Abandoned',
  terminated: 'Ended',
}

/**
 * The four steps of a normal order, marked done, current or upcoming. A
 * session that ended another way keeps the steps it reached and gains a final
 * step saying how it ended.
 */
export function progressSteps(state: SessionState, reachedOrdering: boolean): ProgressStep[] {
  const ending = OTHER_ENDINGS[state]
  if (ending) {
    const reached = reachedOrdering ? 2 : 1
    return [
      ...HAPPY_PATH.slice(0, reached).map((step) => ({ label: step.label, status: 'done' as const })),
      { label: ending, status: 'current' as const },
    ]
  }
  const position = HAPPY_PATH.findIndex((step) => step.state === state)
  return HAPPY_PATH.map((step, index) => ({
    label: step.label,
    status: index < position ? 'done' : index === position ? 'current' : 'upcoming',
  }))
}

export function isFinal(state: SessionState): boolean {
  return state === 'submitted' || state in OTHER_ENDINGS
}

/** What an empty conversation says: a car waiting at the speaker, or the line waiting for a call. */
export function emptyConversationHint(following: boolean, opening = 'A car has pulled up.'): string {
  return following
    ? 'Waiting for a call. What the caller and the agent say will appear here.'
    : `${opening} Say hello to start the order.`
}
