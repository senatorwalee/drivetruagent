import { useCallback, useState } from 'react'

/** Independent home page options; keep the original designs available. */
export const HOME_DESIGNS = [
  { id: 'current', label: 'Current' },
  { id: 'new', label: 'New' },
  { id: 'business', label: 'Business' },
] as const

export type HomeDesign = (typeof HOME_DESIGNS)[number]['id']

const STORAGE_KEY = 'drivetruagent.home-design'
const DEFAULT_DESIGN: HomeDesign = 'business'

function isHomeDesign(value: string | null): value is HomeDesign {
  return HOME_DESIGNS.some((design) => design.id === value)
}

/** The chosen design. `?design=` in the address wins, then the last choice made. */
function initialDesign(): HomeDesign {
  const fromAddress = new URLSearchParams(window.location.search).get('design')
  if (isHomeDesign(fromAddress)) return fromAddress
  const stored = window.localStorage.getItem(STORAGE_KEY)
  return isHomeDesign(stored) ? stored : DEFAULT_DESIGN
}

/** Remembers which home page design is showing, across reloads and in the address bar. */
export function useHomeDesign(): [HomeDesign, (design: HomeDesign) => void] {
  const [design, setDesign] = useState<HomeDesign>(initialDesign)

  const choose = useCallback((next: HomeDesign) => {
    setDesign(next)
    window.localStorage.setItem(STORAGE_KEY, next)
    const address = new URL(window.location.href)
    address.searchParams.set('design', next)
    window.history.replaceState(null, '', address)
  }, [])

  return [design, choose]
}
