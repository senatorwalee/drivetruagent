/**
 * Pure helpers that turn unmet requests into the words and sizes the insights
 * page shows. No React and no network, so each is tested on its own.
 */
import type { UnmetRequest } from '../api/client'

/** How the closest menu item related to the request, in plain words. */
export function matchLabel(match: string): string {
  return match === 'close' ? 'close match' : 'not on the menu'
}

/** How long ago something was last asked for, the way a person says it. */
export function lastAsked(iso: string, nowMs: number): string {
  const minutes = Math.max(0, Math.floor((nowMs - Date.parse(iso)) / 60_000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.floor(hours / 24)
  return days === 1 ? 'yesterday' : `${days} days ago`
}

/** A bar's length as a share of the most-asked request, in percent. */
export function share(times: number, most: number): number {
  return most > 0 ? Math.max(4, Math.round((times / most) * 100)) : 0
}

/** "1 time", "41 times". */
export function timesLabel(times: number): string {
  return times === 1 ? '1 time' : `${times} times`
}

/** What the restaurant might do about the most-asked request. */
export function suggestion(top: UnmetRequest): string {
  const asked = `Guests asked for "${top.request}" ${timesLabel(top.times)}.`
  return top.match === 'close'
    ? `${asked} The menu has something close but not this. Consider offering it, or a line that says it isn't available.`
    : `${asked} Nothing on the menu matches. Consider adding it, if it fits the kitchen.`
}
