import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, type WebCall, type WebPhoneLine } from '../api/client'
import { IDLE, MICROPHONE, VoiceCall, type VoiceState } from '../voice/voiceCall'
import { chooseLine, describeLineProblem } from './words'

/** The browser parts a call needs. Tests pass stand-ins; jsdom has none. */
export interface PhoneParts {
  getMicrophone: () => Promise<MediaStream>
  createConnection: () => RTCPeerConnection
  createSpeaker: () => HTMLAudioElement
}

/** How often the page checks which lines are busy, while not on a call. */
const LINE_CHECK_INTERVAL_MS = 5000
/** On a call, how often the page checks the backend is still on it. */
const CALL_CHECK_INTERVAL_MS = 2000

const BROWSER: PhoneParts = {
  getMicrophone: () => navigator.mediaDevices.getUserMedia(MICROPHONE),
  createConnection: () => new RTCPeerConnection(),
  createSpeaker: () => Object.assign(new Audio(), { autoplay: true }),
}

export interface WebPhone {
  /** Who the call goes to, once the key is checked. */
  restaurantName: string | null
  /** Why the line can't be used: no key, a wrong one, switched off. */
  lineProblem: string | null
  /** The lines to call on, one per number the server has. */
  lines: WebPhoneLine[]
  /** The line a call goes on; kept while a call is going. */
  line: number
  chooseLine: (line: number) => void
  voice: VoiceState
  /** Why the call was not put through, if it was turned away. */
  turnedAway: WebCall['turned_away'] | null
  /** When the call went live (ms since the epoch), and how long it lasted once over. */
  liveSince: number | null
  lastedMs: number | null
  muted: boolean
  call: () => void
  hangUp: () => void
  toggleMute: () => void
}

/**
 * One phone call from the web phone page at a time, on the line picked (or
 * the link's). The call is a VoiceCall like the mic button's, but rings the
 * web phone's endpoints with the key from the link, and tells the backend
 * when the audio is up so the agent's opening notice is not lost.
 */
export function useWebPhone(key: string | null, parts: PhoneParts = BROWSER, lineFromLink: number | null = null): WebPhone {
  const [voice, setVoice] = useState<VoiceState>(IDLE)
  const onCall = voice.status === 'connecting' || voice.status === 'live'
  const info = useQuery({
    queryKey: ['web-phone-line', key],
    queryFn: () => api.webPhoneLine(key ?? ''),
    enabled: key !== null,
    retry: false,
    refetchInterval: onCall ? false : LINE_CHECK_INTERVAL_MS,
  })
  const lines = info.data?.lines ?? []
  const [picked, setPicked] = useState<number | null>(null)
  const line = chooseLine(lines, picked, lineFromLink)
  const [turnedAway, setTurnedAway] = useState<WebCall['turned_away'] | null>(null)
  const [liveSince, setLiveSince] = useState<number | null>(null)
  const [lastedMs, setLastedMs] = useState<number | null>(null)
  const [muted, setMuted] = useState(false)
  const [callKey, setCallKey] = useState<string | null>(null)
  const current = useRef<VoiceCall | null>(null)

  // When the backend hangs up (after the order, a handoff or a goodbye),
  // the browser is not always told, and the line would stay open with
  // nobody on it. So the page asks, and hangs up too.
  const callState = useQuery({
    queryKey: ['web-call', key, callKey],
    queryFn: () => api.webCallState(key ?? '', callKey ?? ''),
    enabled: key !== null && callKey !== null && voice.status === 'live',
    refetchInterval: CALL_CHECK_INTERVAL_MS,
  })
  const backendHungUp = callState.data?.live === false
  useEffect(() => {
    if (backendHungUp) void current.current?.stop()
  }, [backendHungUp])

  const call = useCallback(() => {
    if (key === null || current.current) return
    // Keep the line for the call, even once it shows as busy.
    setPicked(line)
    // Made, and started playing, inside the tap: phones only play sound a
    // tap has started.
    const speaker = parts.createSpeaker()
    void speaker.play?.()?.catch(() => undefined)
    let answeredKey: string | null = null
    const next = new VoiceCall({
      getMicrophone: parts.getMicrophone,
      createConnection: parts.createConnection,
      sendOffer: async (sdp) => {
        try {
          const answered = await api.callWebPhone(key, sdp, line)
          answeredKey = answered.call_key
          setCallKey(answered.call_key)
          setTurnedAway(answered.turned_away ?? null)
          return answered.answer_sdp
        } catch (error) {
          throw new Error(describeLineProblem(error))
        }
      },
      hangUp: async () => {
        if (answeredKey) await api.hangUpWebPhone(key, answeredKey)
      },
      connected: async () => {
        if (answeredKey) await api.webPhoneConnected(key, answeredKey)
      },
      speaker,
    })
    current.current = next
    setCallKey(null)
    setTurnedAway(null)
    setLiveSince(null)
    setLastedMs(null)
    setMuted(false)
    let wentLive: number | null = null
    next.subscribe((latest) => {
      setVoice(latest)
      if (latest.status === 'live' && wentLive === null) {
        wentLive = Date.now()
        setLiveSince(wentLive)
      }
      if (latest.status === 'ended' || latest.status === 'failed') {
        if (wentLive !== null) setLastedMs(Date.now() - wentLive)
        if (current.current === next) current.current = null
      }
    })
    void next.start()
  }, [key, parts, line])

  const hangUp = useCallback(() => {
    void current.current?.stop()
  }, [])

  const toggleMute = useCallback(() => {
    setMuted((was) => {
      current.current?.setMuted(!was)
      return !was
    })
  }, [])

  // Leaving the page hangs up.
  useEffect(() => () => void current.current?.stop(), [])

  const lineProblem = key === null ? "This link has no key. Ask for the full link." : info.error ? describeLineProblem(info.error) : null

  return {
    restaurantName: info.data?.restaurant_name ?? null,
    lineProblem,
    lines,
    line,
    chooseLine: setPicked,
    voice,
    turnedAway,
    liveSince,
    lastedMs,
    muted,
    call,
    hangUp,
    toggleMute,
  }
}
