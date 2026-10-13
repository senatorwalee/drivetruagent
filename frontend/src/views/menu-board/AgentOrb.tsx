import { AGENT_STATE_LABEL } from '../../session/presentation'
import type { AgentState } from '../../session/types'
import styles from './MenuBoard.module.css'

const BARS = 15

/**
 * The agent's presence: a core that breathes, rings that ripple outwards
 * while it speaks and inwards while it listens, and a small wave beneath.
 * All of the motion is driven from CSS by the `data-state` attribute.
 */
export function AgentOrb({ state }: { state: AgentState }) {
  return (
    <div className={styles.orbArea} data-state={state}>
      <div role="img" aria-label={`Agent: ${AGENT_STATE_LABEL[state]}`} className={styles.orb}>
        <span className={styles.ring} />
        <span className={styles.ring} />
        <span className={styles.ring} />
        <span className={styles.halo} />
        <span className={styles.core}>
          <span className={styles.coreCentre} />
        </span>
      </div>
      <p className={styles.stateLabel} aria-live="polite">
        {AGENT_STATE_LABEL[state]}
      </p>
      <div aria-hidden="true" className={styles.wave}>
        {Array.from({ length: BARS }, (_, index) => (
          <span key={index} className={styles.bar} />
        ))}
      </div>
    </div>
  )
}
