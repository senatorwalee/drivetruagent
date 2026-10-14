import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../api/client'
import { IDLE, VoiceCall, type VoiceState } from './voiceCall'

export interface Voice extends VoiceState {
  /** Voice can be used: the backend has a key for it and the session is open. */
  available: boolean
  /** A call is being set up or is under way. */
  active: boolean
  start: () => void
  stop: () => void
}

interface Options {
  sessionId: string | undefined
  /** The backend can make voice calls. */
  enabled: boolean
  /** The session has ended: any call is over. */
  ended: boolean
  /** The presenter paused: the microphone is muted. */
  paused: boolean
}

function browserDeps(sessionId: string, speaker: HTMLAudioElement) {
  return {
    getMicrophone: () => navigator.mediaDevices.getUserMedia({ audio: true }),
    createConnection: () => new RTCPeerConnection(),
    sendOffer: (sdp: string) => api.startVoice(sessionId, sdp),
    hangUp: () => api.endVoice(sessionId),
    speaker,
  }
}

/**
 * The voice call for the session on screen. At most one at a time; it is
 * hung up when the session ends or another session starts.
 */
export function useVoiceCall({ sessionId, enabled, ended, paused }: Options): Voice {
  const [state, setState] = useState<VoiceState>(IDLE)
  const call = useRef<VoiceCall | null>(null)
  const speaker = useRef<HTMLAudioElement | null>(null)

  const stop = useCallback(() => {
    const current = call.current
    call.current = null
    void current?.stop()
    setState(IDLE)
  }, [])

  const start = useCallback(() => {
    if (!sessionId || call.current) return
    speaker.current ??= Object.assign(new Audio(), { autoplay: true })
    const next = new VoiceCall(browserDeps(sessionId, speaker.current))
    call.current = next
    next.subscribe((latest) => {
      setState(latest)
      // A call that is over can be started again.
      if (latest.status === 'ended' || latest.status === 'failed') {
        if (call.current === next) call.current = null
      }
    })
    void next.start()
  }, [sessionId])

  // A new session, or this one ending, hangs up whatever call there was.
  useEffect(() => stop, [sessionId, stop])
  useEffect(() => {
    if (ended && call.current) stop()
  }, [ended, stop])
  useEffect(() => {
    call.current?.setMuted(paused)
  }, [paused, state.status])

  return {
    ...state,
    available: enabled && Boolean(sessionId) && !ended,
    active: state.status === 'connecting' || state.status === 'live',
    start,
    stop,
  }
}
