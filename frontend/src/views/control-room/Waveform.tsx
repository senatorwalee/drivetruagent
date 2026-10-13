import { AGENT_STATE_LABEL } from '../../session/presentation'
import type { AgentState } from '../../session/types'
import styles from './ControlRoom.module.css'

/** Bar heights, as percentages, that give the wave an uneven, voice-like shape. */
const SHAPE = [
  22, 38, 54, 30, 66, 82, 48, 96, 70, 44, 88, 60, 34, 76, 100, 58, 40, 84, 64, 92, 50, 72, 36, 80, 56, 94, 42, 68, 86, 46, 62, 78,
  32, 52, 28, 20,
]

/**
 * The agent as a waveform. It moves while the agent speaks or listens and
 * lies flat otherwise. Each bar gets its own height, speed and offset so the
 * wave never looks like a loop.
 */
export function Waveform({ state }: { state: AgentState }) {
  return (
    <div className={styles.voice} data-state={state}>
      <div className={styles.panelHead}>
        <h2 className={styles.panelTitle}>Voice</h2>
        <p className={styles.stateLabel} aria-live="polite">
          {AGENT_STATE_LABEL[state]}
        </p>
      </div>
      <div role="img" aria-label={`Agent: ${AGENT_STATE_LABEL[state]}`} className={styles.wave}>
        {SHAPE.map((height, index) => (
          <span
            key={index}
            className={styles.waveBar}
            style={{
              height: `${height}%`,
              animationDuration: `${(0.9 + (index % 5) * 0.07).toFixed(2)}s`,
              animationDelay: `-${(index * 137) % 900}ms`,
            }}
          />
        ))}
      </div>
    </div>
  )
}
