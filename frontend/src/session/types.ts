/**
 * The shapes the screen is drawn from. These describe what a viewer sees, not
 * what the API returns; timeline.ts turns one into the other.
 */

/** What the agent is doing right now, as shown by the orb or waveform. */
export type AgentState = 'ready' | 'listening' | 'working' | 'speaking' | 'paused' | 'ended'

/**
 * What the backend decided about a tool call. `read` is a lookup that changes
 * nothing, so the policy engine was not involved.
 */
export type DecisionKind = 'allow' | 'reject' | 'confirm' | 'escalate' | 'read'

export interface TranscriptLine {
  id: number
  speaker: 'customer' | 'agent'
  text: string
}

export interface Decision {
  id: number
  tool: string
  /** The arguments in a short readable form, such as "fries · size=large". */
  summary: string
  kind: DecisionKind
  /** The rule or error code behind the decision, if there was one. */
  reason: string | null
  /** The name of something the backend told the agent it may offer, if anything. */
  suggestion: string | null
  /** The cart version after the call, when the call returned a cart. */
  cartVersion: number | null
}

/** Something the agent has been told to check with the customer first. */
export interface PendingItem {
  quantity: number
  itemCode: string
}

export interface PlacedOrder {
  orderNumber: number
  totalCents: number
}

export interface Timeline {
  transcript: TranscriptLine[]
  decisions: Decision[]
  pending: PendingItem | null
  order: PlacedOrder | null
}
