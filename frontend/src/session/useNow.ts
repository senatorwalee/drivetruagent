import { useEffect, useState } from 'react'

/**
 * The time now, updated once a second. Countdowns and "waiting for" labels
 * are worked out from times the backend gives; this is only the tick.
 */
export function useNow(): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  return now
}
