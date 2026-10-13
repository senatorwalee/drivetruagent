import '@fontsource-variable/archivo'
import '@fontsource/space-mono/400.css'
import '@fontsource/space-mono/700.css'

import { clock, progressSteps } from '../../session/presentation'
import type { LiveSession } from '../../session/useLiveSession'
import { MicrophoneIcon } from '../shared/icons'
import { useComposer } from '../shared/useComposer'
import { ViewSwitcher } from '../shared/ViewSwitcher'
import type { LiveSessionViewProps } from '../shared/viewTypes'
import { AgentDisc } from './AgentDisc'
import { Ledger } from './Ledger'
import styles from './Receipt.module.css'
import { ReceiptTape } from './ReceiptTape'

/** A light, printed look: the order is a receipt and the log is a ledger. */
export function Receipt({ live, view, onViewChange }: LiveSessionViewProps) {
  return (
    <div className={styles.screen}>
      <header className={styles.topBar}>
        <span className={styles.brandName}>{live.agent?.restaurant_name ?? 'Drive-through'}</span>
        <span className={styles.meta}>
          {live.cart?.language ?? 'en'} / {clock(live.elapsedSeconds)}
        </span>
        <ProgressSteps live={live} />
        <div className={styles.controls}>
          <ViewSwitcher
            view={view}
            onChange={onViewChange}
            classes={{ group: styles.switcher, option: styles.switcherOption, selected: styles.switcherSelected }}
          />
          <button type="button" className={styles.button} aria-pressed={live.paused} onClick={live.togglePause}>
            {live.paused ? 'Resume' : 'Pause'}
          </button>
          <button type="button" className={`${styles.button} ${styles.buttonSolid}`} onClick={live.startNewSession}>
            New session
          </button>
        </div>
      </header>

      {live.problem && (
        <p role="alert" className={styles.problem}>
          {live.problem}
        </p>
      )}

      <main className={styles.main}>
        <section aria-label="Agent" className={styles.stage}>
          <AgentDisc state={live.agentState} />
          <Script live={live} />
          <Composer live={live} />
        </section>
        <ReceiptTape live={live} />
        <Ledger decisions={live.timeline.decisions} />
      </main>
    </div>
  )
}

function ProgressSteps({ live }: { live: LiveSession }) {
  if (!live.session) return null
  const steps = progressSteps(live.session.state, live.timeline.decisions.length > 0)
  return (
    <ol aria-label="Session progress" className={styles.steps}>
      {steps.map((step) => (
        <li key={step.label} className={styles.step} data-status={step.status} aria-current={step.status === 'current' ? 'step' : undefined}>
          {step.label}
        </li>
      ))}
    </ol>
  )
}

/** The conversation set like a script: who spoke, then the line. */
function Script({ live }: { live: LiveSession }) {
  const lines = live.timeline.transcript.slice(-4)
  if (lines.length === 0) {
    return <p className={styles.hint}>A car has pulled up. Say hello to start the order.</p>
  }
  return (
    <div aria-label="Conversation" aria-live="polite" className={styles.script}>
      {lines.map((line, index) => (
        <div key={line.id} className={styles.scriptLine} data-speaker={line.speaker} data-latest={index === lines.length - 1}>
          <span className={styles.speaker}>{line.speaker === 'agent' ? 'Agent' : 'Customer'}</span>
          <p className={styles.spoken}>{line.text}</p>
        </div>
      ))}
    </div>
  )
}

function Composer({ live }: { live: LiveSession }) {
  const composer = useComposer(live)
  return (
    <div className={styles.composerArea}>
      {composer.suggestion && (
        <button type="button" className={styles.suggestion} onClick={composer.useSuggestion} disabled={composer.disabled}>
          <span className={styles.suggestionLabel}>Demo script / say</span>
          {composer.suggestion}
        </button>
      )}
      <form className={styles.composer} onSubmit={composer.submit}>
        <button type="button" className={styles.mic} aria-label="Speak (voice is not connected yet)" disabled>
          <MicrophoneIcon />
        </button>
        <label htmlFor="receipt-say" className={styles.visuallyHidden}>
          What the customer says
        </label>
        <input
          id="receipt-say"
          type="text"
          autoComplete="off"
          className={styles.input}
          value={composer.text}
          placeholder={composer.placeholder}
          disabled={composer.disabled}
          onChange={(event) => composer.change(event.target.value)}
        />
        <button type="submit" className={styles.send} disabled={composer.disabled || !composer.text.trim()}>
          Send
        </button>
      </form>
    </div>
  )
}
