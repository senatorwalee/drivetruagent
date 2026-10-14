/**
 * Everything one live session needs, behind a single hook.
 *
 * The hook owns the session's lifecycle (resume or open, start again), keeps
 * the cart and event log fresh, sends what the customer says, and works out
 * what the agent indicator should show. Views receive the result and only
 * draw it; none of them fetches anything.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api, ApiError, type AgentInfo, type Cart, type Menu, type Session } from '../api/client'
import { deriveAgentState, isFinal, runningSeconds, speakingMilliseconds, timerNote } from './presentation'
import { useVoiceCall, type Voice } from '../voice/useVoiceCall'
import { buildTimeline } from './timeline'
import { useNow } from './useNow'
import type { AgentState, Timeline } from './types'

const STORED_SESSION = 'drivetruagent.sessionId'
/** While a turn is in progress, how often to look for new steps. */
const FOLLOW_INTERVAL_MS = 400
/**
 * Between turns, how often to look for something the backend did by itself:
 * asking a silent customer if they are still there, or ending the session.
 */
const IDLE_INTERVAL_MS = 1000

const NO_MODEL = 'No model is configured. In .env, set AGENT_MODEL=demo, or add an OpenAI or Gemini key.'

const EMPTY_TIMELINE: Timeline = { transcript: [], decisions: [], pending: null, order: null }

export interface LiveSession {
  /** `starting` until a session exists; `failed` if the backend is unreachable. */
  status: 'starting' | 'ready' | 'failed'
  /** Something the viewer should be told, in plain words. */
  problem: string | null
  agent: AgentInfo | null
  session: Session | null
  cart: Cart | null
  timeline: Timeline
  agentState: AgentState
  /** How long the session has run, leaving out time spent paused. */
  elapsedSeconds: number
  /** What the backend's timers are counting down to, in words. Null if nothing. */
  timerNote: string | null
  /** For the scripted demo: the next line to type. Null with a real model. */
  suggestedLine: string | null
  /** Display names for item codes, for things not in the cart yet. */
  itemName: (itemCode: string) => string
  /** The backend's timers are stopped. Set by the backend, not kept here. */
  paused: boolean
  /** The voice call, if one is on, and how to start or end one. */
  voice: Voice
  canSend: boolean
  send: (text: string) => void
  startNewSession: () => void
  togglePause: () => void
  /** Views call this as the customer starts and stops typing or talking. */
  setCustomerActive: (active: boolean) => void
}

/** Reuse this tab's session if it is still in progress; otherwise open one. */
async function resumeOrOpen(): Promise<Session> {
  const stored = window.sessionStorage.getItem(STORED_SESSION)
  if (stored) {
    try {
      const session = await api.session(stored)
      if (!isFinal(session.state)) return session
    } catch (error) {
      // A session that no longer exists is simply replaced.
      if (!(error instanceof ApiError)) throw error
    }
  }
  // Each tab takes its own lane, because a lane holds one session at a time.
  const session = await api.openSession(`screen-${crypto.randomUUID().slice(0, 8)}`)
  window.sessionStorage.setItem(STORED_SESSION, session.session_id)
  return session
}

export function useLiveSession(): LiveSession {
  const queryClient = useQueryClient()
  // Bumping the generation makes the query below run again: a new session.
  const [generation, setGeneration] = useState(0)
  const [customerActive, setCustomerActive] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const speakingTimer = useRef<number | undefined>(undefined)

  const agentQuery = useQuery({ queryKey: ['agent'], queryFn: api.agentInfo, staleTime: Infinity })
  const opened = useQuery({
    queryKey: ['live-session', generation],
    queryFn: resumeOrOpen,
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  })
  const sessionId = opened.data?.session_id

  const message = useMutation({
    mutationFn: (text: string) => api.sendMessage(sessionId!, text),
    onSuccess: (reply) => {
      setSpeaking(true)
      window.clearTimeout(speakingTimer.current)
      speakingTimer.current = window.setTimeout(() => setSpeaking(false), speakingMilliseconds(reply.reply))
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['session', sessionId] }),
  })
  useEffect(() => () => window.clearTimeout(speakingTimer.current), [])

  // Read when a refetch is due, so polling speeds up as soon as a call starts.
  const callLive = useRef(false)
  const sessionKey = ['session', sessionId, 'state']
  const sessionQuery = useQuery({
    queryKey: sessionKey,
    queryFn: () => api.session(sessionId!),
    initialData: opened.data,
    enabled: Boolean(sessionId),
    // While a turn is running, each tool's work is saved as it finishes, so
    // polling shows items arriving one by one instead of all at the end.
    // Between turns the backend's timers may act, so the screen keeps
    // looking, more slowly. A paused or finished session cannot change.
    refetchInterval: (query) => followInterval(query.state.data, message.isPending || callLive.current),
  })
  // Pausing is the backend's state: its timers are what must stop. The
  // answer replaces the session held here, so the button follows at once.
  const pause = useMutation({
    mutationFn: (shouldPause: boolean) => (shouldPause ? api.pause(sessionId!) : api.resume(sessionId!)),
    onSuccess: (session) => queryClient.setQueryData(sessionKey, session),
  })

  const session = sessionQuery.data ?? null
  const ended = session ? isFinal(session.state) : false
  const paused = session?.timers.paused ?? false
  const agent = agentQuery.data ?? null

  const voice = useVoiceCall({ sessionId, enabled: Boolean(agent?.voice_available), ended, paused })
  useEffect(() => {
    callLive.current = voice.active
  }, [voice.active])

  // During a turn or a call, each step is saved as it happens, so the screen
  // looks often and shows items arriving one by one.
  const follow = {
    enabled: Boolean(sessionId),
    refetchInterval: followInterval(session ?? undefined, message.isPending || voice.active),
  } as const
  const cartQuery = useQuery({ queryKey: ['session', sessionId, 'cart'], queryFn: () => api.cart(sessionId!), ...follow })
  const eventsQuery = useQuery({
    queryKey: ['session', sessionId, 'events'],
    queryFn: () => api.events(sessionId!),
    ...follow,
  })
  const cart = cartQuery.data ?? null
  const language = cart?.language ?? 'en'
  const menuQuery = useQuery({ queryKey: ['menu', language], queryFn: () => api.menu(language), staleTime: Infinity })

  const timeline = useMemo(() => (eventsQuery.data ? buildTimeline(eventsQuery.data) : EMPTY_TIMELINE), [eventsQuery.data])
  const itemName = useItemNames(menuQuery.data)
  const now = useNow()

  // Something the agent said without being asked, such as "Are you still
  // there?", is shown as speech too.
  const lastAgentLine = timeline.transcript.findLast((line) => line.speaker === 'agent')?.id
  const spokenLine = useRef(lastAgentLine)
  useEffect(() => {
    if (lastAgentLine === undefined || lastAgentLine === spokenLine.current) return
    const firstLoad = spokenLine.current === undefined && !eventsQuery.isFetchedAfterMount
    spokenLine.current = lastAgentLine
    // On a call the speaking state comes from the call itself.
    if (firstLoad || message.isPending || voice.active) return
    setSpeaking(true)
    window.clearTimeout(speakingTimer.current)
    speakingTimer.current = window.setTimeout(() => setSpeaking(false), 2500)
  }, [lastAgentLine, message.isPending, voice.active, eventsQuery.isFetchedAfterMount])

  const customerTurns = timeline.transcript.filter((line) => line.speaker === 'customer').length
  // Typing is off during a call: the voice model keeps its own conversation,
  // and a typed message would go to the text model instead.
  const canSend = Boolean(sessionId) && Boolean(agent?.available) && !ended && !paused && !message.isPending && !voice.active

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim()
      if (trimmed && canSend) message.mutate(trimmed)
    },
    [canSend, message],
  )

  const startNewSession = useCallback(() => {
    window.sessionStorage.removeItem(STORED_SESSION)
    window.clearTimeout(speakingTimer.current)
    setSpeaking(false)
    voice.stop()
    message.reset()
    setGeneration((current) => current + 1)
  }, [message, voice])

  return {
    status: opened.isError ? 'failed' : sessionId ? 'ready' : 'starting',
    problem: voice.problem ?? describeProblem(opened.error, message.error, agent),
    agent,
    session,
    cart,
    timeline,
    agentState: deriveAgentState({
      paused,
      ended,
      waitingForReply: message.isPending,
      speaking: speaking || voice.agentSpeaking,
      customerActive: customerActive || voice.customerSpeaking,
    }),
    elapsedSeconds: session ? runningSeconds(session, now) : 0,
    timerNote: ended ? null : timerNote(session?.timers, now),
    suggestedLine: agent?.scripted && !ended ? (agent.suggested_lines[customerTurns] ?? null) : null,
    itemName,
    paused,
    voice,
    canSend,
    send,
    startNewSession,
    togglePause: useCallback(() => {
      if (sessionId && !ended) pause.mutate(!paused)
    }, [sessionId, ended, paused, pause]),
    setCustomerActive,
  }
}

function describeProblem(openError: unknown, sendError: unknown, agent: AgentInfo | null): string | null {
  if (openError) return 'Cannot reach the backend. Start it with `make dev`, then reload.'
  if (sendError instanceof ApiError && sendError.code === 'agent_unavailable') {
    return NO_MODEL
  }
  if (sendError) return 'That message did not go through. Try again.'
  if (agent && !agent.available) {
    return NO_MODEL
  }
  return null
}

function useItemNames(menu: Menu | undefined): (itemCode: string) => string {
  return useMemo(() => {
    const names = new Map(
      (menu?.categories ?? []).flatMap((category) => category.items.map((item) => [item.item_code, item.item_name])),
    )
    return (itemCode: string) => names.get(itemCode) ?? itemCode
  }, [menu])
}

/** How often to refetch, given what the session is doing. False: not at all. */
function followInterval(session: Session | undefined, turnInProgress: boolean): number | false {
  if (turnInProgress) return FOLLOW_INTERVAL_MS
  if (!session || isFinal(session.state) || session.timers.paused) return false
  return IDLE_INTERVAL_MS
}
