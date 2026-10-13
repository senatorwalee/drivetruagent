/**
 * A small in-memory stand-in for the backend's handoff endpoints, for testing
 * the staff screen as a whole. It keeps a list of packages and changes them
 * the way the real backend would.
 */
import { vi } from 'vitest'

import type { HandoffPackage, HandoffSummary } from '../api/client'

interface Request {
  method: string
  path: string
  body: Record<string, unknown>
}

const SUMMARY_KEYS = [
  'handoff_id',
  'session_id',
  'lane',
  'language',
  'status',
  'reason',
  'created_at',
  'acknowledged_by',
  'item_count',
  'total_cents',
] as const

function summaryOf(handoff: HandoffPackage): HandoffSummary {
  return Object.fromEntries(SUMMARY_KEYS.map((key) => [key, handoff[key]])) as unknown as HandoffSummary
}

export function installFakeStaffBackend(initial: HandoffPackage[]) {
  const requests: Request[] = []
  let handoffs = initial.map((handoff) => structuredClone(handoff))
  let refuseNextResolveWith: string | null = null
  let reachable = true

  const respond = (status: number, body: unknown) =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
  const refuse = (status: number, code: string) => respond(status, { error: { code, message: code, details: {} } })
  const replace = (updated: HandoffPackage) => {
    handoffs = handoffs.map((handoff) => (handoff.handoff_id === updated.handoff_id ? updated : handoff))
    return respond(200, updated)
  }

  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (!reachable) return Promise.reject(new TypeError('Failed to fetch'))
      const path = String(input).replace(/^\/api/, '').split('?')[0]
      const method = init?.method ?? 'GET'
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {}
      requests.push({ method, path, body })

      if (path === '/handoffs') return respond(200, handoffs.map(summaryOf))
      const match = /^\/handoffs\/([^/]+)(\/\w+)?$/.exec(path)
      const handoff = handoffs.find((candidate) => candidate.handoff_id === match?.[1])
      if (!match || !handoff) return refuse(404, 'handoff_not_found')
      const action = match[2] ?? ''
      if (action === '') return respond(200, handoff)
      if (handoff.status === 'resolved') return refuse(409, 'handoff_resolved')

      if (action === '/acknowledge') {
        if (handoff.status !== 'open') return respond(200, handoff)
        return replace({ ...handoff, status: 'acknowledged', acknowledged_by: String(body.by), acknowledged_at: new Date().toISOString() })
      }
      if (action === '/resolve') {
        if (refuseNextResolveWith) {
          const code = refuseNextResolveWith
          refuseNextResolveWith = null
          return refuse(409, code)
        }
        const placed = body.outcome === 'order_placed'
        return replace({
          ...handoff,
          status: 'resolved',
          outcome: placed ? 'order_placed' : 'cancelled',
          note: (body.note as string | null) ?? null,
          resolved_at: new Date().toISOString(),
          order: placed
            ? {
                order_id: 'order-1',
                order_number: 12,
                session_id: handoff.session_id,
                language: handoff.language,
                lines: handoff.cart.lines,
                item_count: handoff.cart.item_count,
                subtotal_cents: handoff.cart.subtotal_cents,
                discounts: handoff.cart.discounts,
                discount_cents: handoff.cart.discount_cents,
                tax_cents: handoff.cart.tax_cents,
                total_cents: handoff.cart.total_cents,
                submitted_at: new Date().toISOString(),
              }
            : null,
        })
      }
      return refuse(404, 'handoff_not_found')
    }),
  )

  return {
    requests,
    /** The requests that changed something, as "METHOD path". */
    actions: () => requests.filter((request) => request.method === 'POST'),
    /** The next attempt to resolve is refused with this error code. */
    refuseNextResolve: (code: string) => {
      refuseNextResolveWith = code
    },
    goOffline: () => {
      reachable = false
    },
  }
}
