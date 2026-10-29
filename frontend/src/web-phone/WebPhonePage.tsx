import { useEffect } from 'react'

import { useNow } from '../session/useNow'
import { MicrophoneIcon } from '../views/shared/icons'
import { type PhoneParts, useWebPhone } from './useWebPhone'
import styles from './WebPhone.module.css'
import { initials, keyFromHash, lineFromHash, lineLabel, speakingLine, statusLine, turnedAwayLine } from './words'

function PhoneIcon() {
  return (
    <svg aria-hidden="true" width="30" height="30" viewBox="0 0 24 24" fill="currentColor">
      <path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1z" />
    </svg>
  )
}

/**
 * The web phone: a page to open on a phone and ring the restaurant from,
 * without a phone number. Frontdesk answers it as a phone call. Opened from
 * a link that carries the key: /call#key=….
 */
export function WebPhonePage({ parts }: { parts?: PhoneParts }) {
  const phone = useWebPhone(keyFromHash(window.location.hash), parts, lineFromHash(window.location.hash))
  const now = useNow()
  const { voice } = phone
  const active = voice.status === 'connecting' || voice.status === 'live'
  const liveFor = phone.lastedMs ?? (phone.liveSince === null ? null : now - phone.liveSince)
  const status = phone.lineProblem ?? (phone.restaurantName === null ? 'Connecting to the line…' : statusLine(voice, liveFor))
  const speaking = speakingLine(voice)
  const turnedAway = turnedAwayLine(phone.turnedAway)
  const name = phone.restaurantName ?? 'Frontdesk'
  const canCall = phone.restaurantName !== null && phone.lineProblem === null

  useEffect(() => {
    document.title = `Call ${name}`
  }, [name])

  return (
    <main className={styles.page}>
      <p className={styles.brand}>Frontdesk web phone</p>

      <section className={styles.contact} aria-live="polite">
        <div className={`${styles.avatar} ${voice.agentSpeaking ? styles.avatarSpeaking : ''}`} aria-hidden="true">
          {initials(name)}
        </div>
        <h1 className={styles.name}>{name}</h1>
        <p className={`${styles.status} ${voice.status === 'failed' || phone.lineProblem ? styles.problem : ''}`}>{status}</p>
        {turnedAway && <p className={styles.problem}>{turnedAway}</p>}
        {speaking && <p className={styles.speaking}>{speaking}</p>}
      </section>

      {phone.lines.length > 1 && (
        <fieldset className={styles.lines} disabled={active}>
          <legend className={styles.linesLabel}>Call as</legend>
          {phone.lines.map((each) => {
            const mine = each.line === phone.line
            const taken = each.busy && !(mine && active)
            return (
              <label key={each.line} className={`${styles.line} ${mine ? styles.lineChosen : ''}`}>
                <input
                  type="radio"
                  name="line"
                  value={each.line}
                  checked={mine}
                  disabled={taken}
                  onChange={() => phone.chooseLine(each.line)}
                />
                <span className={styles.lineNumber}>{lineLabel(each)}</span>
                <span className={styles.lineState}>{taken ? 'On a call' : mine && active ? 'You' : 'Free'}</span>
              </label>
            )
          })}
        </fieldset>
      )}

      <div className={styles.controls}>
        {active ? (
          <>
            <button
              type="button"
              className={`${styles.control} ${styles.mute} ${phone.muted ? styles.muteOn : ''}`}
              onClick={phone.toggleMute}
              aria-pressed={phone.muted}
            >
              <span className={styles.circle}>
                <MicrophoneIcon />
              </span>
              <span className={styles.label}>Mute</span>
            </button>
            <button type="button" className={`${styles.control} ${styles.end}`} onClick={phone.hangUp}>
              <span className={styles.circle}>
                <PhoneIcon />
              </span>
              <span className={styles.label}>End</span>
            </button>
          </>
        ) : (
          <button type="button" className={`${styles.control} ${styles.call}`} onClick={phone.call} disabled={!canCall}>
            <span className={styles.circle}>
              <PhoneIcon />
            </span>
            <span className={styles.label}>{voice.status === 'idle' ? 'Call' : 'Call again'}</span>
          </button>
        )}
      </div>
    </main>
  )
}
