/**
 * Just enough routing for a few pages: which path is showing, and a way to
 * change it without reloading. A router library would be more than this needs.
 */
import { useSyncExternalStore } from 'react'

export const HOME_PATH = '/'
export const STAFF_PATH = '/staff'
/** Phone orders waiting to be collected. */
export const PICKUPS_PATH = '/pickups'
/** Phone numbers that have called, and blocking them. */
export const CALLERS_PATH = '/callers'
/** What happened in each conversation, in a note written after it. */
export const CONVERSATIONS_PATH = '/conversations'
export const INSIGHTS_PATH = '/insights'
export const MENU_PATH = '/menu'
/** A restaurant's first visit: who it is, its menu, and going live. */
export const ONBOARDING_PATH = '/onboarding'
export const LIVE_PATH = '/live'
/** The live views, following the newest phone call instead of a lane. */
export const CALLS_PATH = '/calls'
/** The web phone: ring the restaurant from a phone's browser. */
export const CALL_PATH = '/call'

function subscribe(onChange: () => void): () => void {
  window.addEventListener('popstate', onChange)
  return () => window.removeEventListener('popstate', onChange)
}

/** The path in the address bar, kept current as the visitor moves around. */
export function usePath(): string {
  return useSyncExternalStore(subscribe, () => window.location.pathname)
}

/** Go to another page of the app. The Back button returns as usual. */
export function navigate(path: string): void {
  if (path === window.location.pathname) return
  window.history.pushState(null, '', path)
  // pushState does not announce itself, so tell whoever is listening.
  window.dispatchEvent(new PopStateEvent('popstate'))
}
