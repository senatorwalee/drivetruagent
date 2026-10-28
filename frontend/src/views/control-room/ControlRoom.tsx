import { HomeLink } from '../../app/HomeLink'
import { CALLS_PATH, INSIGHTS_PATH, LIVE_PATH, MENU_PATH, STAFF_PATH } from '../../app/navigation'
import { PageLink } from '../../app/PageLink'
import { clock, emptyConversationHint, modelCost } from '../../session/presentation'
import type { LiveSession } from '../../session/useLiveSession'
import { MicButton } from '../shared/MicButton'
import { ShopBoard } from '../shared/ShopBoard'
import { useComposer } from '../shared/useComposer'
import { ViewSwitcher } from '../shared/ViewSwitcher'
import type { LiveSessionViewProps } from '../shared/viewTypes'
import { CartTable } from './CartTable'
import styles from './ControlRoom.module.css'
import { DecisionLog } from './DecisionLog'
import { Waveform } from './Waveform'

/** A dense console for showing how the system works: the decision log leads. */
export function ControlRoom({ live, view, onViewChange }: LiveSessionViewProps) {
  return (
    <div className={styles.screen}>
      <header className={styles.topBar}>
        <HomeLink />
        <div className={styles.brand}>
          <span aria-hidden="true" className={styles.beacon} data-live={live.status === 'ready' && live.agentState !== 'ended'} />
          <span className={styles.brandName}>{live.agent?.restaurant_name ?? 'Restaurant'}</span>
        </div>
        <Readouts live={live} />
        <div className={styles.controls}>
          <ViewSwitcher
            view={view}
            onChange={onViewChange}
            classes={{ group: styles.switcher, option: styles.switcherOption, selected: styles.switcherSelected }}
          />
          <PageLink to={STAFF_PATH} className={styles.button}>
            Staff
          </PageLink>
          <PageLink to={INSIGHTS_PATH} className={styles.button}>
            Insights
          </PageLink>
          <PageLink to={MENU_PATH} className={styles.button}>
            Menu
          </PageLink>
          <PageLink to={live.following ? LIVE_PATH : CALLS_PATH} className={styles.button}>
            {live.following ? 'Drive-through' : 'Phone calls'}
          </PageLink>
          <button type="button" className={styles.button} aria-pressed={live.paused} onClick={live.togglePause}>
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
        <section aria-label="Agent" className={styles.column}>
          <Waveform state={live.agentState} />
          <TranscriptLog live={live} />
          <Composer live={live} />
        </section>
        <DecisionLog decisions={live.timeline.decisions} />
        <CartTable live={live} />
        <ShopBoard week={live.board.week} stock={live.board.stock} className={styles.board} />
      </main>
    </div>
  )
}

/** The session's vital signs, read straight from the backend's state. */
function Readouts({ live }: { live: LiveSession }) {
  const readouts = [
    { label: 'State', value: live.session?.state ?? '—', highlight: true },
    { label: 'Lane', value: live.session?.lane ?? '—' },
    { label: 'Language', value: live.cart?.language ?? '—' },
    { label: 'Cart version', value: live.cart ? String(live.cart.version) : '—' },
    { label: 'Model', value: live.agent?.model ?? 'none' },
    { label: 'Model cost', value: live.cost ? modelCost(live.cost) : '—' },
    { label: 'Session time', value: clock(live.elapsedSeconds) },
    { label: 'Timer', value: live.timerNote ?? '—' },
  ]
  return (
    <dl className={styles.readouts}>
      {readouts.map((readout) => (
        <div key={readout.label} className={styles.readout}>
          <dt>{readout.label}</dt>
          <dd data-highlight={readout.highlight ?? false}>{readout.value}</dd>
        </div>
      ))}
    </dl>
  )
}

function TranscriptLog({ live }: { live: LiveSession }) {
  const lines = live.timeline.transcript.slice(-5)
  if (lines.length === 0) {
    return <p className={styles.hint}>{emptyConversationHint(live.following, 'No conversation yet.')}</p>
  }
  return (
    <div aria-label="Conversation" aria-live="polite" className={styles.transcript}>
      {lines.map((line, index) => (
        <div key={line.id} className={styles.transcriptLine} data-speaker={line.speaker} data-latest={index === lines.length - 1}>
          <span className={styles.speaker}>{line.speaker === 'agent' ? 'agent' : 'cust'}</span>
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
          <span className={styles.suggestionLabel}>demo script · say</span>
          {composer.suggestion}
        </button>
      )}
      <form className={styles.composer} onSubmit={composer.submit}>
        <MicButton voice={live.voice} className={styles.mic} />
        <label htmlFor="control-room-say" className={styles.visuallyHidden}>
          What the customer says
        </label>
        <input
          id="control-room-say"
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
