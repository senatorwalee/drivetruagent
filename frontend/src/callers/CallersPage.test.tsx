/** The pickups and callers pages against a stand-in backend. */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'
import type { Caller, Pickup } from '../api/client'

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString()

const WAITING: Pickup = {
  order_id: 'order-41',
  order_number: 41,
  submitted_at: minutesAgo(12),
  total_cents: 1450,
  lines: [
    { quantity: 2, item_name: 'Cheeseburger' },
    { quantity: 1, item_name: 'Fries' },
  ],
  caller_last_four: '0123',
  status: 'waiting',
  marked_by: null,
  marked_at: null,
}

const REGULAR: Caller = {
  caller_id: 'caller-1',
  phone_e164: '+14165550123',
  first_call_at: minutesAgo(60 * 24 * 30),
  last_call_at: minutesAgo(5),
  call_count: 6,
  history_opted_out: false,
  order_count: 5,
  recent_no_shows: 0,
  recent_calls_without_order: 1,
  status: 'normal',
  status_reason: null,
  status_changed_by: null,
  status_changed_at: null,
  standing: 'normal',
}

const LATE: Caller = {
  ...REGULAR,
  caller_id: 'caller-2',
  phone_e164: '+16475550199',
  call_count: 3,
  order_count: 2,
  recent_no_shows: 2,
  standing: 'flagged',
}

interface Sent {
  method: string
  path: string
  body: Record<string, unknown>
}

function installBackend({ pickups = [WAITING], callers = [REGULAR, LATE] }: { pickups?: Pickup[]; callers?: Caller[] } = {}) {
  const sent: Sent[] = []
  let shownPickups = pickups.map((pickup) => ({ ...pickup }))
  let shownCallers = callers.map((caller) => ({ ...caller }))
  const respond = (status: number, body?: unknown) =>
    Promise.resolve(
      new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
    )

  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^\/api/, '')
      const method = init?.method ?? 'GET'
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {}
      if (method !== 'GET') sent.push({ method, path, body })

      if (path === '/pickups') return respond(200, shownPickups)
      if (path === '/callers' && method === 'GET') return respond(200, shownCallers)
      const pickup = /^\/pickups\/(.+)$/.exec(path)
      if (pickup) {
        shownPickups = shownPickups.map((p) =>
          p.order_id === pickup[1] ? { ...p, status: body.status as Pickup['status'], marked_by: String(body.by), marked_at: new Date().toISOString() } : p,
        )
        return respond(200, shownPickups.find((p) => p.order_id === pickup[1]))
      }
      const status = /^\/callers\/(.+)\/status$/.exec(path)
      if (status) {
        const to = body.status as Caller['status']
        shownCallers = shownCallers.map((c) =>
          c.caller_id === status[1]
            ? { ...c, status: to, standing: to, status_reason: String(body.reason), status_changed_by: String(body.by) }
            : c,
        )
        return respond(200, shownCallers.find((c) => c.caller_id === status[1]))
      }
      const deleted = /^\/callers\/(.+)$/.exec(path)
      if (deleted && method === 'DELETE') {
        shownCallers = shownCallers.filter((c) => c.caller_id !== deleted[1])
        return respond(204)
      }
      return respond(404, { error: { code: 'not_found', message: 'not found', details: {} } })
    }),
  )
  return sent
}

function open(path: string) {
  window.history.replaceState(null, '', path)
  render(<App />)
}

beforeEach(() => window.localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('the pickups page', () => {
  it('lists phone orders waiting, with what to hand over', async () => {
    installBackend()
    open('/pickups')

    const waiting = await screen.findByRole('list', { name: 'Waiting, oldest first' })
    const [order] = within(waiting).getAllByRole('listitem')
    expect(order).toHaveTextContent('Order 41')
    expect(order).toHaveTextContent('2 × Cheeseburger, 1 × Fries')
    expect(order).toHaveTextContent('$14.50 · ••• ••• 0123 · placed 12 min ago')
  })

  it('marks a no-show, signed with the name typed on the staff screen', async () => {
    window.localStorage.setItem('drivetruagent.staffName', 'Sam')
    const sent = installBackend()
    open('/pickups')

    await userEvent.click(await screen.findByRole('button', { name: 'No-show, order 41' }))

    expect(sent).toEqual([{ method: 'POST', path: '/pickups/order-41', body: { status: 'no_show', by: 'Sam' } }])
    const marked = await screen.findByRole('list', { name: 'Marked, newest first' })
    expect(within(marked).getByText(/no-show · Sam/)).toBeInTheDocument()
    expect(screen.getByText('Nothing waiting. A phone order will appear here as soon as it is placed.')).toBeInTheDocument()
  })

  it('lets a wrong mark be put right', async () => {
    const sent = installBackend({ pickups: [{ ...WAITING, status: 'no_show', marked_by: 'Sam', marked_at: minutesAgo(2) }] })
    open('/pickups')

    await userEvent.click(await screen.findByRole('button', { name: 'Change to picked up, order 41' }))

    expect(sent[0].body).toEqual({ status: 'picked_up', by: 'staff' })
  })
})

describe('the callers page', () => {
  it('lists numbers with the digits hidden, their counts and standing', async () => {
    installBackend()
    open('/callers')

    const late = await screen.findByRole('listitem', { name: 'Number ending 0199' })
    expect(late).toHaveTextContent('••• ••• 0199')
    expect(late).not.toHaveTextContent('647')
    expect(late).toHaveTextContent('flagged')
    expect(late).toHaveTextContent('2 recent no-shows')
    expect(late).toHaveTextContent('3 calls · 2 orders')
  })

  it('shows a whole number only when asked', async () => {
    installBackend()
    open('/callers')
    const late = await screen.findByRole('listitem', { name: 'Number ending 0199' })

    await userEvent.click(within(late).getByRole('button', { name: 'Show number' }))

    expect(late).toHaveTextContent('+1 647 555 0199')
  })

  it('blocks a number only with a reason, and says what blocking does', async () => {
    const sent = installBackend()
    open('/callers')
    const regular = await screen.findByRole('listitem', { name: 'Number ending 0123' })

    await userEvent.click(within(regular).getByRole('button', { name: 'Block' }))
    const form = within(regular).getByRole('form', { name: 'Block this number' })
    expect(form).toHaveTextContent('told it can’t order by phone')
    const confirm = within(form).getByRole('button', { name: 'Block' })
    expect(confirm).toBeDisabled()

    await userEvent.type(within(form).getByRole('textbox', { name: /Reason/ }), 'Abusive calls')
    await userEvent.click(confirm)

    expect(sent).toEqual([
      { method: 'POST', path: '/callers/caller-1/status', body: { status: 'blocked', reason: 'Abusive calls', by: 'staff' } },
    ])
    await waitFor(() => expect(regular).toHaveTextContent('Blocked by staff: Abusive calls'))
    expect(within(regular).getByRole('button', { name: 'Unblock' })).toBeInTheDocument()
  })

  it('deletes a number only once it is confirmed', async () => {
    const sent = installBackend()
    open('/callers')
    const late = await screen.findByRole('listitem', { name: 'Number ending 0199' })

    await userEvent.click(within(late).getByRole('button', { name: 'Delete' }))
    expect(sent).toEqual([])
    await userEvent.click(within(late).getByRole('button', { name: 'Delete number' }))

    expect(sent).toEqual([{ method: 'DELETE', path: '/callers/caller-2', body: {} }])
    await waitFor(() => expect(screen.queryByRole('listitem', { name: 'Number ending 0199' })).not.toBeInTheDocument())
  })

  it('is reachable from the staff screen', async () => {
    installBackend({ callers: [] })
    open('/pickups')

    await userEvent.click(await screen.findByRole('link', { name: 'Callers' }))

    expect(await screen.findByText('No calls yet. A number appears here after its first call.')).toBeInTheDocument()
  })
})
