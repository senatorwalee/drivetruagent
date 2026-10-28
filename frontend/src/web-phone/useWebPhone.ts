import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, type WebCall } from '../api/client'
import { IDLE, MICROPHONE, VoiceCall, type VoiceState } from '../voice/voiceCall'
import { describeLineProblem } from './words'

/** The browser parts a call needs. Tests pass stand-ins; jsdom has none. */
export interface PhoneParts {
  getMicrophone: () => Promise<MediaStream>
  createConnection: () => RTCPeerConnection
  createSpeaker: () => HTMLAudioElement
}

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
 * One phone call from the web phone page at a time. The call is a VoiceCall
 * like the mic button's, but rings the web phone's endpoints with the key
 * from the link, and tells the backend when the audio is up so the agent's
 * opening notice is not lost.
 */
export function useWebPhone(key: string | null, parts: PhoneParts = BROWSER): WebPhone {
  const line = useQuery({
    queryKey: ['web-phone-line', key],
    queryFn: () => api.webPhoneLine(key ?? ''),
    enabled: key !== null,
    retry: false,
  })
  const [voice, setVoice] = useState<VoiceState>(IDLE)
  const [turnedAway, setTurnedAway] = useState<WebCall['turned_away'] | null>(null)
  const [liveSince, setLiveSince] = useState<number | null>(null)
  const [lastedMs, setLastedMs] = useState<number | null>(null)
  const [muted, setMuted] = useState(false)
  const current = useRef<VoiceCall | null>(null)

  const call = useCallback(() => {
    if (key === null || current.current) return
    // Made, and started playing, inside the tap: phones only play sound a
    // tap has started.
    const speaker = parts.createSpeaker()
    void speaker.play?.()?.catch(() => undefined)
    let callKey: string | null = null
    const next = new VoiceCall({
      getMicrophone: parts.getMicrophone,
      createConnection: parts.createConnection,
      sendOffer: async (sdp) => {
        try {
          const answered = await api.callWebPhone(key, sdp)
          callKey = answered.call_key
          setTurnedAway(answered.turned_away ?? null)
          return answered.answer_sdp
        } catch (error) {
          throw new Error(describeLineProblem(error))
        }
      },
      hangUp: async () => {
        if (callKey) await api.hangUpWebPhone(key, callKey)
      },
      connected: async () => {
        if (callKey) await api.webPhoneConnected(key, callKey)
      },
      speaker,
    })
    current.current = next
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
  }, [key, parts])

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

  const lineProblem = key === null ? "This link has no key. Ask for the full link." : line.error ? describeLineProblem(line.error) : null

  return {
    restaurantName: line.data?.restaurant_name ?? null,
    lineProblem,
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
