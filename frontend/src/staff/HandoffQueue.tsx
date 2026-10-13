import { itemsLabel, money } from '../session/presentation'
import styles from './Staff.module.css'
import type { StaffDesk } from './useStaffDesk'
import { statusLabel, waitingFor } from './words'

/** The list of handoffs: who is waiting, why, and for how long. */
export function HandoffQueue({ desk }: { desk: StaffDesk }) {
  return (
    <nav aria-label="Handoffs" className={styles.queue}>
      <div role="group" aria-label="Show" className={styles.tabs}>
        <button type="button" aria-pressed={desk.tab === 'waiting'} className={styles.tab} onClick={() => desk.setTab('waiting')}>
          Waiting <span className={styles.count}>{desk.waitingCount}</span>
        </button>
        <button type="button" aria-pressed={desk.tab === 'closed'} className={styles.tab} onClick={() => desk.setTab('closed')}>
          Closed
        </button>
      </div>

      {desk.queue.length === 0 && (
        <p className={styles.empty}>
          {desk.tab === 'waiting' ? 'Nobody is waiting. A session handed to a person will appear here by itself.' : 'Nothing has been closed yet.'}
        </p>
      )}

      <ul className={styles.entries}>
        {desk.queue.map((handoff) => (
          <li key={handoff.handoff_id}>
            <button
              type="button"
              className={styles.entry}
              aria-current={handoff.handoff_id === desk.selectedId}
              data-status={handoff.status}
              onClick={() => desk.select(handoff.handoff_id)}
            >
              <span className={styles.entryTitle}>{handoff.reason.title}</span>
              <span className={styles.entryStatus} data-status={handoff.status}>
                {statusLabel(handoff)}
              </span>
              <span className={styles.entryMeta}>
                {handoff.lane} · {itemsLabel(handoff.item_count)} · {money(handoff.total_cents)}
              </span>
              {handoff.status !== 'resolved' && <span className={styles.entryWait}>{waitingFor(handoff, desk.now)}</span>}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  )
}
