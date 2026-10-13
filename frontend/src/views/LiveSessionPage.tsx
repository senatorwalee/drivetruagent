import type { ComponentType } from 'react'

import { useLiveSession } from '../session/useLiveSession'
import { ControlRoom } from './control-room/ControlRoom'
import { MenuBoard } from './menu-board/MenuBoard'
import { Receipt } from './receipt/Receipt'
import { useViewPreference } from './shared/useViewPreference'
import type { LiveSessionViewProps, ViewId } from './shared/viewTypes'

const VIEW_COMPONENTS: Record<ViewId, ComponentType<LiveSessionViewProps>> = {
  'menu-board': MenuBoard,
  receipt: Receipt,
  'control-room': ControlRoom,
}

/**
 * The live session screen. One session, three ways of looking at it: the
 * session's state lives here, above the views, so switching view mid-order
 * changes how it looks and nothing else.
 */
export function LiveSessionPage() {
  const live = useLiveSession()
  const [view, setView] = useViewPreference()
  const View = VIEW_COMPONENTS[view]

  return <View live={live} view={view} onViewChange={setView} />
}
