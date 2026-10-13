/**
 * Small pure helpers every view uses to turn state into words and numbers:
 * money, elapsed time, the agent indicator's state, and the progress steps.
 */
import type { SessionState } from '../api/client'
import type { AgentState } from './types'

export function money(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

/** Seconds as m:ss, the way a timer reads. */
export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
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
