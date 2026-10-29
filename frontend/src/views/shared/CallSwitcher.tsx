import type { PhoneCall } from '../../api/client'
import { clock } from '../../session/presentation'
import { useNow } from '../../session/useNow'
import styles from './CallSwitcher.module.css'

interface CallSwitcherProps {
  calls: PhoneCall[]
  /** The call on screen. */
  onScreen: string | null
  /** The call picked to stay on screen; null follows the newest. */
  watching: string | null
  onWatch: (sessionId: string | null) => void
}

/**
 * The phone calls in progress, to pick which one the screen shows. It floats
 * at the bottom so each view keeps its own layout.
 */
export function CallSwitcher({ calls, onScreen, watching, onWatch }: CallSwitcherProps) {
  const now = useNow()
  // Oldest first, so a call keeps its place as new ones ring.
  const inOrder = [...calls].reverse()

  return (
    <nav aria-label="Calls in progress" className={styles.switcher}>
      <button type="button" className={styles.option} aria-pressed={watching === null} onClick={() => onWatch(null)}>
        Newest
      </button>
      {inOrder.map((call) => (
        <button
          key={call.session_id}
          type="button"
          className={styles.option}
          aria-pressed={watching !== null && call.session_id === onScreen}
          onClick={() => onWatch(call.session_id)}
        >
          <span aria-hidden="true" className={styles.dot} data-on-screen={call.session_id === onScreen} />
          <span className={styles.number}>{call.caller_last_four ? `…${call.caller_last_four}` : 'Withheld'}</span>
          <span className={styles.duration}>{clock(Math.max(0, Math.floor((now - Date.parse(call.started_at)) / 1000)))}</span>
        </button>
      ))}
    </nav>
  )
}
