import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/figtree'
import '@fontsource-variable/jetbrains-mono'

import { STAFF_PATH } from '../../app/navigation'
import { PageLink } from '../../app/PageLink'
import { clock, progressSteps } from '../../session/presentation'
import type { LiveSession } from '../../session/useLiveSession'
import { CheckIcon, PauseIcon, PlayIcon } from '../shared/icons'
import { MicButton } from '../shared/MicButton'
import { ShopBoard } from '../shared/ShopBoard'
import { useComposer } from '../shared/useComposer'
import { ViewSwitcher } from '../shared/ViewSwitcher'
import type { LiveSessionViewProps } from '../shared/viewTypes'
import { AgentOrb } from './AgentOrb'
import { DecisionStrip } from './DecisionStrip'
import styles from './MenuBoard.module.css'
import { OrderPanel } from './OrderPanel'

/** A warm, dark screen like a drive-through sign at night: orb and order first. */
export function MenuBoard({ live, view, onViewChange }: LiveSessionViewProps) {
  return (
    <div className={styles.screen}>
      <header className={styles.topBar}>
        <div className={styles.brand}>
          <span className={styles.brandName}>{live.agent?.restaurant_name ?? 'Drive-through'}</span>
          <span className={styles.meta}>
            {(live.cart?.language ?? 'en').toUpperCase()} · {clock(live.elapsedSeconds)}
            {live.timerNote && ` · ${live.timerNote}`}
          </span>
        </div>
        <ProgressSteps live={live} />
        <div className={styles.controls}>
          <ViewSwitcher
            view={view}
            onChange={onViewChange}
            classes={{ group: styles.switcher, option: styles.switcherOption, selected: styles.switcherSelected }}
          />
          <PageLink to={STAFF_PATH} className={styles.button}>
            Staff
          </PageLink>
          <button type="button" className={styles.button} aria-pressed={live.paused} onClick={live.togglePause}>
            {live.paused ? <PlayIcon /> : <PauseIcon />}
            {live.paused ? 'Resume' : 'Pause'}
          </button>
          <button type="button" className={`${styles.button} ${styles.buttonRaised}`} onClick={live.startNewSession}>
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
          <AgentOrb state={live.agentState} />
          <Conversation live={live} />
          <Composer live={live} />
        </section>
        <OrderPanel live={live} />
        <DecisionStrip decisions={live.timeline.decisions} />
        <ShopBoard week={live.board.week} stock={live.board.stock} className={styles.board} />
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
          {step.status === 'done' && <CheckIcon />}
          {step.status === 'current' && <span className={styles.stepDot} />}
          {step.label}
        </li>
      ))}
    </ol>
  )
}

/** The last few lines; the newest thing the agent said is the largest. */
function Conversation({ live }: { live: LiveSession }) {
  const lines = live.timeline.transcript.slice(-3)
  if (lines.length === 0) {
    return <p className={styles.hint}>A car has pulled up. Say hello to start the order.</p>
  }
  return (
    <div aria-label="Conversation" aria-live="polite" className={styles.conversation}>
      {lines.map((line, index) => (
        <p key={line.id} className={styles.bubble} data-speaker={line.speaker} data-latest={index === lines.length - 1}>
          {line.text}
        </p>
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
          <span className={styles.suggestionLabel}>Demo script · say</span>
          {composer.suggestion}
        </button>
      )}
      <form className={styles.composer} onSubmit={composer.submit}>
        <MicButton voice={live.voice} className={styles.mic} />
        <label htmlFor="menu-board-say" className={styles.visuallyHidden}>
          What the customer says
        </label>
        <input
          id="menu-board-say"
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
