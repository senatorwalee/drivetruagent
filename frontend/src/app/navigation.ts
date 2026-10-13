/**
 * Just enough routing for two pages: which path is showing, and a way to
 * change it without reloading. A router library would be more than this needs.
 */
import { useSyncExternalStore } from 'react'

export const STAFF_PATH = '/staff'
export const LIVE_PATH = '/'

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
