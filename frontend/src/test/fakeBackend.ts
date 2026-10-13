/**
 * A tiny in-memory stand-in for the backend, for testing the page as a whole.
 * It answers the same paths the real API does and keeps one session's state.
 */
import { vi } from 'vitest'

import type { AgentEvent, Cart, Session } from '../api/client'
import { agentInfo } from './fixtures'

interface Request {
  method: string
  path: string
}

/** Timers as the backend reports them for a session that has just started. */
function runningTimers(): Session['timers'] {
  return {
    paused: false,
    paused_at: null,
    paused_seconds: 0,
    nudge_at: new Date(Date.now() + 20_000).toISOString(),
    closes_at: null,
    hand_off_at: new Date(Date.now() + 180_000).toISOString(),
  }
}

export function installFakeBackend() {
  const requests: Request[] = []
  let sessionCount = 0
  let session: Session | null = null
  let events: AgentEvent[] = []
  let cart: Cart | null = null
  let seq = 1

  const record = (kind: AgentEvent['kind'], payload: Record<string, unknown>) => {
    events.push({ seq: seq++, turn_id: 'turn', kind, payload, created_at: new Date().toISOString() })
  }

  const openSession = (lane: string): Session => {
    sessionCount += 1
    session = {
      session_id: `session-${sessionCount}`,
      lane,
      state: 'greeting',
      language: null,
      started_at: new Date().toISOString(),
      ended_at: null,
      end_reason: null,
      business_date: '2026-10-12',
      timers: runningTimers(),
    }
    events = []
    cart = {
      session_id: session.session_id,
      state: 'greeting',
      language: 'en',
      version: 0,
      item_count: 0,
      subtotal_cents: 0,
      discounts: [],
      discount_cents: 0,
      tax_cents: 0,
      total_cents: 0,
      lines: [],
    }
    return session
  }

  /** Whatever is said, a cookie is added. Enough to see a turn play out. */
  const takeTurn = (text: string) => {
    record('user_message', { text })
    cart = {
      ...cart!,
      state: 'ordering',
      version: cart!.version + 1,
      item_count: cart!.item_count + 1,
      subtotal_cents: 149,
      tax_cents: 19,
      total_cents: 168,
      lines: [
        {
          line_no: 1,
          item_code: 'cookie',
          item_name: 'Chocolate chip cookie',
          quantity: cart!.item_count + 1,
          options: [],
          unit_price_cents: 149,
          line_total_cents: 149,
        },
      ],
    }
    session = { ...session!, state: 'ordering' }
    record('tool_call', { call_id: `c${seq}`, name: 'add_item', arguments: { item_code: 'cookie', quantity: 1, selections: [] } })
    record('tool_result', { call_id: `c${seq - 1}`, name: 'add_item', result: { decision: 'allow', cart: { version: cart.version } } })
    record('assistant_message', { text: 'One cookie. Anything else?' })
    return { reply: 'One cookie. Anything else?', cart, tools_used: ['add_item'] }
  }

  const respond = (status: number, body: unknown) =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))

  const notFound = () => respond(404, { error: { code: 'session_not_found', message: 'No such session.', details: {} } })

  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^\/api/, '').split('?')[0]
      const method = init?.method ?? 'GET'
      requests.push({ method, path })
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, string>) : {}

      if (path === '/agent') return respond(200, agentInfo)
      if (path === '/menu') return respond(200, { categories: [] })
      if (path === '/handoffs') return respond(200, [])
      if (path === '/sessions' && method === 'POST') return respond(201, openSession(body.lane))

      const match = /^\/sessions\/([^/]+)(\/\w+)?$/.exec(path)
      if (!match || !session || match[1] !== session.session_id) return notFound()
      const resource = match[2] ?? ''
      if (resource === '') return respond(200, session)
      if (resource === '/cart') return respond(200, cart)
      if (resource === '/events') return respond(200, events)
      if (resource === '/messages' && method === 'POST') return respond(200, takeTurn(body.text))
      if (resource === '/pause' && method === 'POST') {
        const paused = { paused: true, paused_at: new Date().toISOString(), paused_seconds: 0, nudge_at: null, closes_at: null, hand_off_at: null }
        session = { ...session, timers: paused }
        return respond(200, session)
      }
      if (resource === '/resume' && method === 'POST') {
        session = { ...session, timers: runningTimers() }
        return respond(200, session)
      }
      return notFound()
    }),
  )

  return {
    requests,
    /** The backend's timer asks the silent customer if they are there. */
    nudge: () => {
      record('timer', { action: 'nudge', rule: 'silence', after_seconds: 20 })
      record('assistant_message', { text: 'Are you still there?' })
    },
    /** The backend's timer closes a session nobody answered in. */
    abandon: () => {
      record('timer', { action: 'abandon', rule: 'silence_timeout', after_seconds: 40 })
      session = { ...session!, state: 'abandoned', ended_at: new Date().toISOString(), end_reason: 'silence_timeout' }
      cart = { ...cart!, state: 'abandoned' }
    },
    sessionsOpened: () => requests.filter((request) => request.method === 'POST' && request.path === '/sessions').length,
  }
}
