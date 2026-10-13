import { useCallback, useState } from 'react'

import { VIEWS, type ViewId } from './viewTypes'

const STORAGE_KEY = 'drivetruagent.view'
const DEFAULT_VIEW: ViewId = 'menu-board'

function isViewId(value: string | null): value is ViewId {
  return VIEWS.some((view) => view.id === value)
}

/** The chosen view. `?view=` in the address wins, then the last choice made. */
function initialView(): ViewId {
  const fromAddress = new URLSearchParams(window.location.search).get('view')
  if (isViewId(fromAddress)) return fromAddress
  const stored = window.localStorage.getItem(STORAGE_KEY)
  return isViewId(stored) ? stored : DEFAULT_VIEW
}

/** Remembers which view is showing, across reloads and in the address bar. */
export function useViewPreference(): [ViewId, (view: ViewId) => void] {
  const [view, setView] = useState<ViewId>(initialView)

  const choose = useCallback((next: ViewId) => {
    setView(next)
    window.localStorage.setItem(STORAGE_KEY, next)
    const address = new URL(window.location.href)
    address.searchParams.set('view', next)
    window.history.replaceState(null, '', address)
  }, [])

  return [view, choose]
}
