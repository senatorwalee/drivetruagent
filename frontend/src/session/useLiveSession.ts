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
import { deriveAgentState, isFinal, speakingMilliseconds } from './presentation'
import { buildTimeline } from './timeline'
import type { AgentState, Timeline } from './types'

const STORED_SESSION = 'drivetruagent.sessionId'
/** While a turn is in progress, how often to look for new steps. */
const FOLLOW_INTERVAL_MS = 400

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
  elapsedSeconds: number
  /** For the scripted demo: the next line to type. Null with a real model. */
  suggestedLine: string | null
  /** Display names for item codes, for things not in the cart yet. */
  itemName: (itemCode: string) => string
  paused: boolean
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
  const [paused, setPaused] = useState(false)
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

  // While a turn is running, each tool's work is saved as it finishes, so
  // polling shows items arriving one by one instead of all at the end.
  const follow = { enabled: Boolean(sessionId), refetchInterval: message.isPending ? FOLLOW_INTERVAL_MS : false } as const
  const sessionQuery = useQuery({
    queryKey: ['session', sessionId, 'state'],
    queryFn: () => api.session(sessionId!),
    initialData: opened.data,
    ...follow,
  })
  const cartQuery = useQuery({ queryKey: ['session', sessionId, 'cart'], queryFn: () => api.cart(sessionId!), ...follow })
  const eventsQuery = useQuery({
    queryKey: ['session', sessionId, 'events'],
    queryFn: () => api.events(sessionId!),
    ...follow,
  })

  const session = sessionQuery.data ?? null
  const cart = cartQuery.data ?? null
  const language = cart?.language ?? 'en'
  const menuQuery = useQuery({ queryKey: ['menu', language], queryFn: () => api.menu(language), staleTime: Infinity })

  const timeline = useMemo(() => (eventsQuery.data ? buildTimeline(eventsQuery.data) : EMPTY_TIMELINE), [eventsQuery.data])
  const itemName = useItemNames(menuQuery.data)
  const ended = session ? isFinal(session.state) : false
  const elapsedSeconds = useElapsedSeconds(session, paused)

  const agent = agentQuery.data ?? null
  const customerTurns = timeline.transcript.filter((line) => line.speaker === 'customer').length
  const canSend = Boolean(sessionId) && Boolean(agent?.available) && !ended && !paused && !message.isPending

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
    setPaused(false)
    message.reset()
    setGeneration((current) => current + 1)
  }, [message])

  return {
    status: opened.isError ? 'failed' : sessionId ? 'ready' : 'starting',
    problem: describeProblem(opened.error, message.error, agent),
    agent,
    session,
    cart,
    timeline,
    agentState: deriveAgentState({
      paused,
      ended,
      waitingForReply: message.isPending,
      speaking,
      customerActive,
    }),
    elapsedSeconds,
    suggestedLine: agent?.scripted && !ended ? (agent.suggested_lines[customerTurns] ?? null) : null,
    itemName,
    paused,
    canSend,
    send,
    startNewSession,
    togglePause: useCallback(() => setPaused((current) => !current), []),
    setCustomerActive,
  }
}

function describeProblem(openError: unknown, sendError: unknown, agent: AgentInfo | null): string | null {
  if (openError) return 'Cannot reach the backend. Start it with `make dev`, then reload.'
  if (sendError instanceof ApiError && sendError.code === 'agent_unavailable') {
    return 'No model is configured. Set AGENT_MODEL=demo in .env, or add an OpenAI key.'
  }
  if (sendError) return 'That message did not go through. Try again.'
  if (agent && !agent.available) {
    return 'No model is configured. Set AGENT_MODEL=demo in .env, or add an OpenAI key.'
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

/** Seconds since the session started, ticking once a second while it runs. */
function useElapsedSeconds(session: Session | null, paused: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  const running = Boolean(session) && !session?.ended_at && !paused

  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])

  if (!session) return 0
  const end = session.ended_at ? Date.parse(session.ended_at) : now
  return Math.max(0, (end - Date.parse(session.started_at)) / 1000)
}
