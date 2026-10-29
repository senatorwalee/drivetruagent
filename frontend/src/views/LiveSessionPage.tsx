import type { ComponentType } from 'react'

import { useLiveSession } from '../session/useLiveSession'
import { ControlRoom } from './control-room/ControlRoom'
import { MenuBoard } from './menu-board/MenuBoard'
import { Receipt } from './receipt/Receipt'
import { CallSwitcher } from './shared/CallSwitcher'
import { useViewPreference } from './shared/useViewPreference'
import type { LiveSessionViewProps, ViewId } from './shared/viewTypes'

const VIEW_COMPONENTS: Record<ViewId, ComponentType<LiveSessionViewProps>> = {
  'menu-board': MenuBoard,
  receipt: Receipt,
  'control-room': ControlRoom,
}

/**
 * The live session screen. One session, three ways of looking at it: the
 * session's own lane, or with `follow="phone"` the newest phone call (or the
 * one picked, when several are going at once). The
 * session's state lives here, above the views, so switching view mid-order
 * changes how it looks and nothing else.
 */
export function LiveSessionPage({ follow }: { follow?: 'phone' }) {
  const live = useLiveSession({ follow })
  const [view, setView] = useViewPreference()
  const View = VIEW_COMPONENTS[view]
  // Something to switch between: calls at once, or a call picked to go back from.
  const switching = live.following && (live.calls.length > 1 || live.watching !== null)

  return (
    <>
      <View live={live} view={view} onViewChange={setView} />
      {switching && (
        <CallSwitcher calls={live.calls} onScreen={live.session?.session_id ?? null} watching={live.watching} onWatch={live.watch} />
      )}
    </>
  )
}
