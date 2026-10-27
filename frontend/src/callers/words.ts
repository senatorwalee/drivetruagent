/**
 * Pure helpers that turn pickups and callers into the words the pickups and
 * callers pages show. No React and no network, so each is tested on its own.
 */
import { ApiError, type Caller, type CallerStatus, type Pickup, type Standing } from '../api/client'
import { lastAsked as ago } from '../insights/words'
import { STAFF_NAME_KEY } from '../staff/useStaffDesk'

/** A number with all but its last four digits hidden, for a screen others can see. */
export function maskedPhone(e164: string): string {
  return `••• ••• ${e164.slice(-4)}`
}

/** A whole number, spaced the way it is read out: +1 416 555 0123. */
export function spacedPhone(e164: string): string {
  const northAmerican = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164)
  return northAmerican ? `+1 ${northAmerican[1]} ${northAmerican[2]} ${northAmerican[3]}` : e164
}

/** "2 × Cheeseburger, 1 × Fries". */
export function pickupLines(pickup: Pick<Pickup, 'lines'>): string {
  return pickup.lines.map((line) => `${line.quantity} × ${line.item_name}`).join(', ')
}

/** Where a pickup stands, with who marked it and when. */
export function pickupStatusLabel(pickup: Pick<Pickup, 'status' | 'marked_by' | 'marked_at'>, nowMs: number): string {
  if (pickup.status === 'waiting') return 'waiting'
  const what = pickup.status === 'picked_up' ? 'picked up' : 'no-show'
  const when = pickup.marked_at ? `, ${ago(pickup.marked_at, nowMs)}` : ''
  return `${what} · ${pickup.marked_by ?? 'staff'}${when}`
}

/** Placed "12 min ago". */
export function placedAgo(pickup: Pick<Pickup, 'submitted_at'>, nowMs: number): string {
  return `placed ${ago(pickup.submitted_at, nowMs)}`
}

const STANDING_LABELS: Record<Standing, string> = {
  normal: 'ok',
  flagged: 'flagged',
  blocked: 'blocked',
  unknown: 'withheld',
}

export function standingLabel(standing: Standing): string {
  return STANDING_LABELS[standing]
}

const STATUS_WORDS: Record<CallerStatus, string> = {
  normal: 'Cleared',
  flagged: 'Flagged',
  blocked: 'Blocked',
}

/** Why a number stands where it does, in a sentence. Empty when nothing is against it. */
export function standingReason(
  caller: Pick<Caller, 'standing' | 'status' | 'recent_no_shows' | 'status_reason' | 'status_changed_by'>,
): string {
  const decided =
    caller.status_reason && caller.status !== 'normal'
      ? `${STATUS_WORDS[caller.status]} by ${caller.status_changed_by ?? 'staff'}: ${caller.status_reason}`
      : ''
  if (caller.standing === 'blocked') return decided
  const noShows = caller.recent_no_shows === 1 ? '1 recent no-show' : `${caller.recent_no_shows} recent no-shows`
  if (caller.standing === 'flagged') return decided || noShows
  // Cleared by staff with no-shows still on record, or nothing at all.
  return caller.recent_no_shows > 0 ? noShows : ''
}

export interface StatusAction {
  status: CallerStatus
  label: string
}

/** What staff can change a number to, from where it is now. */
export function statusActions(status: CallerStatus): StatusAction[] {
  if (status === 'blocked') return [{ status: 'normal', label: 'Unblock' }]
  if (status === 'flagged')
    return [
      { status: 'blocked', label: 'Block' },
      { status: 'normal', label: 'Clear flag' },
    ]
  return [
    { status: 'flagged', label: 'Flag' },
    { status: 'blocked', label: 'Block' },
  ]
}

/** What changing a number's status will do, shown while the reason is typed. */
export function statusEffect(status: CallerStatus): string {
  if (status === 'blocked') return 'Calls from this number will be told it can’t order by phone, and hung up.'
  if (status === 'flagged') return 'Orders over the limit from this number will go to a person to confirm.'
  return 'Calls from this number will be taken as usual. Recent no-shows still count until they are old.'
}

/** "3 calls", "1 order". */
export function counted(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

export { ago }

/** The name a person typed on the staff screen, to sign what they do. */
export function staffName(): string {
  try {
    return window.localStorage.getItem(STAFF_NAME_KEY)?.trim() || 'staff'
  } catch {
    return 'staff'
  }
}

const PROBLEMS: Record<string, string> = {
  caller_not_found: 'This number has already been deleted.',
  order_not_found: 'This order no longer exists.',
  not_a_pickup_order: 'This was not a phone order, so there is no pickup to mark.',
}

/** A failed action, in words for the person who tried it. */
export function describeProblem(error: unknown): string | null {
  if (!error) return null
  if (error instanceof ApiError) return PROBLEMS[error.code] ?? error.message
  return 'That did not go through. Check the backend is running and try again.'
}
