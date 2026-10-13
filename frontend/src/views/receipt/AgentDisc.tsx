import { AGENT_STATE_LABEL } from '../../session/presentation'
import type { AgentState } from '../../session/types'
import styles from './Receipt.module.css'

const TICKS = 12

/**
 * The agent as a solid ink disc with thin rings, beside its status set very
 * large. Motion is driven from CSS by the `data-state` attribute.
 */
export function AgentDisc({ state }: { state: AgentState }) {
  return (
    <div className={styles.agent} data-state={state}>
      <div role="img" aria-label={`Agent: ${AGENT_STATE_LABEL[state]}`} className={styles.disc}>
        <span className={styles.ring} />
        <span className={styles.ring} />
        <span className={styles.ring} />
        <span className={styles.ring} />
        <span className={styles.guide} />
        <span className={styles.core} />
      </div>
      <div className={styles.status}>
        <span className={styles.statusCaption}>Agent status</span>
        <p className={styles.statusWord} aria-live="polite">
          {AGENT_STATE_LABEL[state]}
        </p>
        <div aria-hidden="true" className={styles.ticks}>
          {Array.from({ length: TICKS }, (_, index) => (
            <span key={index} className={styles.tick} />
          ))}
        </div>
      </div>
    </div>
  )
}
