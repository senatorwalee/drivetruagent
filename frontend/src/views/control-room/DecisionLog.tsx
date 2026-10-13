import type { Decision, DecisionKind } from '../../session/types'
import { DECISION_META } from '../shared/decisionMeta'
import styles from './ControlRoom.module.css'

const LEGEND: DecisionKind[] = ['allow', 'reject', 'confirm', 'escalate']

/** Every tool call the agent made, with what the backend decided and why. */
export function DecisionLog({ decisions }: { decisions: Decision[] }) {
  return (
    <section aria-label="Decisions" className={`${styles.column} ${styles.columnWide}`}>
      <div className={styles.panelHead}>
        <h2 className={styles.panelTitle}>Decisions</h2>
        <span className={styles.panelNote}>model proposes · code decides</span>
      </div>

      {decisions.length === 0 ? (
        <p className={styles.hint}>Each tool the agent uses appears here with the backend's decision.</p>
      ) : (
        <ol className={styles.log}>
          {decisions.map((decision, index) => (
            <li key={decision.id} className={styles.entry} data-kind={decision.kind}>
              <span className={styles.entryNumber}>{String(index + 1).padStart(2, '0')}</span>
              <span className={styles.entryCall}>
                <span className={styles.entryTool}>{decision.tool}</span> {decision.summary}
              </span>
              <span className={styles.badge} data-kind={decision.kind}>
                {DECISION_META[decision.kind].icon}
                {DECISION_META[decision.kind].label}
              </span>
              <span className={styles.entryOutcome}>{outcomeOf(decision)}</span>
            </li>
          ))}
        </ol>
      )}

      <ul className={styles.legend}>
        {LEGEND.map((kind) => (
          <li key={kind} className={styles.legendItem} data-kind={kind}>
            <span aria-hidden="true" className={styles.legendSwatch} />
            {DECISION_META[kind].label}: {DECISION_META[kind].meaning}
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The second line of an entry: the rule or error code, and the cart it left. */
function outcomeOf(decision: Decision): string {
  const parts = [decision.reason, decision.cartVersion === null ? null : `cart v${decision.cartVersion}`]
  return parts.filter(Boolean).join(' · ') || DECISION_META[decision.kind].meaning
}
