/**
 * Pure helpers that turn a handoff into the words the staff screen shows.
 * No React and no network, so each is tested on its own.
 */
import { ApiError, type HandoffPackage, type HandoffSummary, type HeldStock } from '../api/client'
import { clock } from '../session/presentation'

/** What stage a handoff is at, in a word or two a person scanning a queue can use. */
export function statusLabel(handoff: Pick<HandoffSummary, 'status' | 'acknowledged_by'>): string {
  if (handoff.status === 'open') return 'waiting'
  if (handoff.status === 'acknowledged') return `with ${handoff.acknowledged_by ?? 'staff'}`
  return 'closed'
}

/** How long a handoff has been waiting, as m:ss. */
export function waitingFor(handoff: Pick<HandoffSummary, 'created_at'>, nowMs: number): string {
  return clock((nowMs - Date.parse(handoff.created_at)) / 1000)
}

/** How a handoff ended, as a sentence. Null while it is still open. */
export function outcomeSentence(handoff: Pick<HandoffPackage, 'outcome' | 'order'>): string | null {
  if (handoff.outcome === 'order_placed') {
    return handoff.order ? `Order ${handoff.order.order_number} was placed.` : 'The order was placed.'
  }
  if (handoff.outcome === 'cancelled') return 'Cancelled. Nothing was ordered.'
  return null
}

/** Whether the cart's stock is still set aside, and until when. */
export function holdLabel(stock: Pick<HeldStock, 'status' | 'held' | 'expires_at'>): string {
  if (stock.status === 'committed') return 'taken for the order'
  if (stock.status === 'released') return 'released'
  if (!stock.held) return 'hold ran out'
  return `held until ${new Date(stock.expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
}

const PROBLEMS: Record<string, string> = {
  insufficient_stock: 'The stock for this order has gone, so it cannot be placed. Cancel it, or check with the kitchen.',
  empty_cart: 'There is nothing in the cart to place.',
  handoff_resolved: 'Someone else has already closed this handoff.',
  handoff_not_found: 'This handoff no longer exists.',
}

/** A failed action, in words for the person who tried it. */
export function describeProblem(error: unknown): string | null {
  if (!error) return null
  if (error instanceof ApiError) return PROBLEMS[error.code] ?? error.message
  return 'That did not go through. Check the backend is running and try again.'
}
