import type { LiveSession } from '../../session/useLiveSession'

/** The three looks the live session screen can be shown in. */
export const VIEWS = [
  { id: 'menu-board', label: 'Menu board' },
  { id: 'receipt', label: 'Receipt' },
  { id: 'control-room', label: 'Control room' },
] as const

export type ViewId = (typeof VIEWS)[number]['id']

/**
 * What every view receives. All three draw the same session; they differ only
 * in how it looks, so swapping view never loses state.
 */
export interface LiveSessionViewProps {
  live: LiveSession
  view: ViewId
  onViewChange: (view: ViewId) => void
}
