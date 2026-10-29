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

import { api, ApiError, type AgentInfo, type Cart, type Cost, type Menu, type PhoneCall, type Session, type WeekOfSpecials } from '../api/client'
import { deriveAgentState, isFinal, runningSeconds, speakingMilliseconds, timerNote } from './presentation'
import { useVoiceCall, type Voice } from '../voice/useVoiceCall'
import { comboStates, stockGroups, type ComboState, type StockGroup } from './board'
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
/** Stock also changes when other lanes order, so it is always kept fresh. */
const STOCK_INTERVAL_MS = 2000
/** The week's specials rarely change; only whether one has sold out does. */
const SPECIALS_INTERVAL_MS = 10000
/** Following phone calls: how often to look for a new one. */
const CALL_LOOKUP_INTERVAL_MS = 2000

const NO_MODEL = 'No model is configured. In .env, set AGENT_MODEL=demo, or add an OpenAI or Gemini key.'
const NO_CALL = 'No call in progress. The next call to the restaurant’s number will show here as it rings.'

const EMPTY_TIMELINE: Timeline = { transcript: [], decisions: [], pending: null, order: null }

export interface LiveSession {
  /**
   * `starting` until a session exists; `failed` if the backend is unreachable;
   * `waiting` when following phone calls and none has come in yet.
   */
  status: 'starting' | 'ready' | 'waiting' | 'failed'
  /** Following phone calls: the screen watches, the caller talks on their phone. */
  following: boolean
  /** Following phone calls: the calls in progress, newest first. */
  calls: PhoneCall[]
  /** Following phone calls: the call picked to stay on screen; null follows the newest. */
  watching: string | null
  watch: (sessionId: string | null) => void
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
  /** What the session's model calls have cost so far. Null until known, or without prices. */
  cost: Cost | null
  /** For the scripted demo: the next line to type. Null with a real model. */
  suggestedLine: string | null
  /** Display names for item codes, for things not in the cart yet. */
  itemName: (itemCode: string) => string
  /** The backend's timers are stopped. Set by the backend, not kept here. */
  paused: boolean
  /** The voice call, if one is on, and how to start or end one. */
  voice: Voice
  /** The week's specials, the combos with what the cart has toward them, and the stock, for the board under the session. */
  board: { week: WeekOfSpecials | null; combos: ComboState[]; stock: StockGroup[] }
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

/**
 * The newest phone call, or the one already on screen when no call is in
 * progress, so a finished call stays up until the next one rings.
 */
async function newestCall(onScreen: Session | null | undefined): Promise<Session | null> {
  const [newest] = await api.phoneCalls()
  if (!newest) return onScreen ?? null
  return api.session(newest.session_id)
}

export function useLiveSession(options: { follow?: 'phone' } = {}): LiveSession {
  const following = options.follow === 'phone'
  const queryClient = useQueryClient()
  // Bumping the generation makes the query below run again: a new session.
  const [generation, setGeneration] = useState(0)
  const [customerActive, setCustomerActive] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const speakingTimer = useRef<number | undefined>(undefined)

  // Following phone calls, several can be going at once: the screen shows the
  // newest unless one is picked.
  const [watching, setWatching] = useState<string | null>(null)

  const agentQuery = useQuery({ queryKey: ['agent'], queryFn: api.agentInfo, staleTime: Infinity })
  const callsQuery = useQuery({
    queryKey: ['phone-calls'],
    queryFn: api.phoneCalls,
    enabled: following,
    refetchInterval: CALL_LOOKUP_INTERVAL_MS,
  })
  const openedKey = ['live-session', following ? 'phone' : 'lane', generation, watching]
  const opened = useQuery({
    queryKey: openedKey,
    queryFn: !following
      ? resumeOrOpen
      : watching
        ? () => api.session(watching)
        : () => newestCall(queryClient.getQueryData<Session | null>(openedKey)),
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
    refetchInterval: following && !watching ? CALL_LOOKUP_INTERVAL_MS : false,
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
    initialData: opened.data ?? undefined,
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

  // On a phone call the caller talks on their own phone, not through this page.
  const voice = useVoiceCall({ sessionId, enabled: !following && Boolean(agent?.voice_available), ended, paused })
  // A phone call is a call in progress too, so the screen keeps up with it.
  const callInProgress = voice.active || (following && Boolean(sessionId) && !ended)
  useEffect(() => {
    callLive.current = callInProgress
  }, [callInProgress])

  // During a turn or a call, each step is saved as it happens, so the screen
  // looks often and shows items arriving one by one.
  const follow = {
    enabled: Boolean(sessionId),
    refetchInterval: followInterval(session ?? undefined, message.isPending || callInProgress),
  } as const
  const cartQuery = useQuery({ queryKey: ['session', sessionId, 'cart'], queryFn: () => api.cart(sessionId!), ...follow })
  const eventsQuery = useQuery({
    queryKey: ['session', sessionId, 'events'],
    queryFn: () => api.events(sessionId!),
    ...follow,
  })
  // Kept up with the events: each new step may be a model call.
  const costQuery = useQuery({
    queryKey: ['session', sessionId, 'cost', eventsQuery.data?.length ?? 0],
    queryFn: () => api.sessionCost(sessionId!),
    enabled: Boolean(sessionId),
    placeholderData: (previous) => previous,
    retry: false,
  })
  const cart = cartQuery.data ?? null
  const language = cart?.language ?? 'en'
  const menuQuery = useQuery({ queryKey: ['menu', language], queryFn: () => api.menu(language), staleTime: Infinity })
  const stockQuery = useQuery({
    queryKey: ['inventory'],
    queryFn: api.inventory,
    // Quick while something is happening in this session, steady otherwise.
    refetchInterval: follow.refetchInterval || STOCK_INTERVAL_MS,
  })
  const weekQuery = useQuery({
    queryKey: ['specials-week', language],
    queryFn: () => api.weekOfSpecials(language),
    refetchInterval: SPECIALS_INTERVAL_MS,
  })
  const combosQuery = useQuery({
    queryKey: ['combos', language],
    queryFn: () => api.combos(language),
    refetchInterval: SPECIALS_INTERVAL_MS,
  })
  const week = weekQuery.data ?? null
  const board = useMemo(
    () => ({
      week,
      combos: comboStates(combosQuery.data ?? [], menuQuery.data, cart),
      stock: stockGroups(menuQuery.data, stockQuery.data ?? [], week),
    }),
    [week, combosQuery.data, menuQuery.data, stockQuery.data, cart],
  )

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
  const canSend =
    !following && Boolean(sessionId) && Boolean(agent?.available) && !ended && !paused && !message.isPending && !voice.active

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim()
      if (trimmed && canSend) message.mutate(trimmed)
    },
    [canSend, message],
  )

  const startNewSession = useCallback(() => {
    // Following calls, this looks for the newest call again instead.
    if (!following) window.sessionStorage.removeItem(STORED_SESSION)
    window.clearTimeout(speakingTimer.current)
    setSpeaking(false)
    voice.stop()
    message.reset()
    setWatching(null)
    setGeneration((current) => current + 1)
  }, [following, message, voice])

  return {
    status: opened.isError ? 'failed' : sessionId ? 'ready' : following && opened.isSuccess ? 'waiting' : 'starting',
    following,
    calls: callsQuery.data ?? [],
    watching,
    watch: setWatching,
    problem:
      following && opened.isSuccess && !sessionId
        ? NO_CALL
        : (voice.problem ?? describeProblem(opened.error, message.error, following ? null : agent)),
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
    cost: costQuery.data?.conversation ?? null,
    suggestedLine: agent?.scripted && !ended ? (agent.suggested_lines[customerTurns] ?? null) : null,
    itemName,
    paused,
    voice,
    board,
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
