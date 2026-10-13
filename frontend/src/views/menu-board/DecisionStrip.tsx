import type { Decision } from '../../session/types'
import { DECISION_META, readableReason } from '../shared/decisionMeta'
import styles from './MenuBoard.module.css'

/** How many recent decisions fit comfortably in the strip. */
const SHOWN = 4

/** What the backend decided about the agent's latest tool calls. */
export function DecisionStrip({ decisions }: { decisions: Decision[] }) {
  const recent = decisions.slice(-SHOWN)
  return (
    <section aria-label="What the system did" className={styles.strip}>
      <h2 className={styles.stripTitle}>What the system did</h2>
      {recent.length === 0 ? (
        <p className={styles.stripEmpty}>Each tool the agent uses shows here with the backend's decision.</p>
      ) : (
        <ol className={styles.decisions}>
          {recent.map((decision) => (
            <li key={decision.id} className={styles.decision} data-kind={decision.kind}>
              <div className={styles.decisionHead}>
                <span>{decision.tool}</span>
                <span className={styles.badge} data-kind={decision.kind}>
                  {DECISION_META[decision.kind].icon}
                  {DECISION_META[decision.kind].label}
                </span>
              </div>
              <div className={styles.decisionDetail}>
                {[decision.summary, readableReason(decision.reason)].filter(Boolean).join(' · ') || '—'}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
