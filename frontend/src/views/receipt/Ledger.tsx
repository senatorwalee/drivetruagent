import type { Decision } from '../../session/types'
import { DECISION_META, decisionWhy } from '../shared/decisionMeta'
import styles from './Receipt.module.css'

/** How many recent rows the ledger shows. */
const SHOWN = 6

/** Every tool call and the backend's decision, as rows in a ledger. */
export function Ledger({ decisions }: { decisions: Decision[] }) {
  const firstShown = Math.max(0, decisions.length - SHOWN)
  const recent = decisions.slice(firstShown)

  return (
    <section aria-label="System ledger" className={styles.ledger}>
      <h2 className={styles.ledgerTitle}>System ledger</h2>
      <div className={styles.ledgerScroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Tool</th>
              <th scope="col">Asked for</th>
              <th scope="col">Decision</th>
              <th scope="col">Why</th>
            </tr>
          </thead>
          <tbody>
            {recent.length === 0 && (
              <tr>
                <td colSpan={5} className={styles.ledgerEmpty}>
                  Each tool the agent uses is written here with the backend's decision.
                </td>
              </tr>
            )}
            {recent.map((decision, index) => (
              <tr key={decision.id} data-kind={decision.kind}>
                <td>{String(firstShown + index + 1).padStart(2, '0')}</td>
                <td>{decision.tool}</td>
                <td>{decision.summary || '—'}</td>
                <td>
                  <span className={styles.verdict} data-kind={decision.kind}>
                    {DECISION_META[decision.kind].label}
                  </span>
                </td>
                <td className={styles.why}>{decisionWhy(decision) ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
