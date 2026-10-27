/** The conversations page against a stand-in backend. */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'
import type { ConversationSummary, SummaryDetail } from '../api/client'

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString()

const ORDERED: ConversationSummary = {
  session_id: 'session-ordered',
  lane: 'lane-1',
  channel: 'drive_thru',
  language: 'en',
  outcome: 'submitted',
  started_at: minutesAgo(7),
  ended_at: minutesAgo(5),
  gist: 'Ordered two cheeseburgers and fries, no trouble.',
  follow_up: null,
  mood: 'pleased',
  written_by: 'model',
  order_number: 41,
  total_cents: 1607,
  handoff_title: null,
  written_at: minutesAgo(5),
}

const HANDED_OFF: ConversationSummary = {
  ...ORDERED,
  session_id: 'session-handed-off',
  lane: 'phone-abc',
  channel: 'phone',
  outcome: 'handed_off',
  ended_at: minutesAgo(2),
  gist: 'Handed to a person: allergen question with no checked answer.',
  follow_up: 'Check the sesame question with the manager before they order.',
  mood: null,
  written_by: 'template',
  order_number: null,
  total_cents: null,
  handoff_title: 'Allergen question with no checked answer',
}

const DETAIL: SummaryDetail = {
  ...ORDERED,
  facts: {
    outcome: 'submitted',
    end_reason: null,
    channel: 'drive_thru',
    language: 'en',
    started_at: ORDERED.started_at,
    ended_at: ORDERED.ended_at,
    duration_seconds: 95,
    customer_turns: 3,
    order: {
      order_number: 41,
      lines: [
        { quantity: 2, item_name: 'Cheeseburger' },
        { quantity: 1, item_name: 'Fries' },
      ],
      total_cents: 1607,
    },
    handoff: null,
    abuse_strikes: 0,
    confusion_strikes: 0,
    flagged_messages: 0,
    unmet_requests: ['oat milk shake'],
    refused: [],
    nudges: 0,
    model_calls: 4,
    input_tokens: 12_000,
    output_tokens: 200,
  },
  model: 'gpt-6-luna',
  rejected_reason: null,
  input_tokens: 900,
  output_tokens: 40,
}

interface Sent {
  method: string
  path: string
}

function installBackend(summaries: ConversationSummary[] = [HANDED_OFF, ORDERED]) {
  const sent: Sent[] = []
  let detail = DETAIL
  const respond = (status: number, body: unknown) =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))

  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const [path, query = ''] = String(input).replace(/^\/api/, '').split('?')
      const method = init?.method ?? 'GET'
      sent.push({ method, path: query ? `${path}?${query}` : path })
      if (path === '/summaries') {
        const outcome = new URLSearchParams(query).get('outcome')
        return respond(
          200,
          summaries.filter((summary) => !outcome || summary.outcome === outcome),
        )
      }
      if (path === `/sessions/${ORDERED.session_id}/summary`) {
        if (method === 'POST') detail = { ...detail, gist: 'Two cheeseburgers and fries, quick and easy.' }
        return respond(200, detail)
      }
      return respond(404, { error: { code: 'not_found', message: 'Not here', details: {} } })
    }),
  )
  return sent
}

function open() {
  window.history.replaceState(null, '', '/conversations')
  render(<App />)
}

afterEach(() => vi.unstubAllGlobals())

describe('the conversations page', () => {
  it('lists a note on each session, newest first, with its outcome and order', async () => {
    installBackend()
    open()

    const rows = within(await screen.findByRole('list', { name: 'Conversations' })).getAllByRole('listitem')

    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('handed off')
    expect(rows[0]).toHaveTextContent('Phone')
    expect(rows[0]).toHaveTextContent('Follow up: Check the sesame question with the manager before they order.')
    expect(rows[0]).toHaveTextContent('written from the log')
    expect(rows[1]).toHaveTextContent('ordered')
    expect(rows[1]).toHaveTextContent('Drive-thru · lane-1')
    expect(rows[1]).toHaveTextContent('#41 · $16.07')
    expect(rows[1]).toHaveTextContent('Ordered two cheeseburgers and fries, no trouble.')
    expect(rows[1]).toHaveTextContent('written by the model · pleased')
  })

  it('shows only the notes to follow up, or one outcome', async () => {
    const sent = installBackend()
    open()
    await screen.findByRole('list', { name: 'Conversations' })

    await userEvent.click(screen.getByRole('button', { name: 'To follow up' }))
    expect(within(screen.getByRole('list', { name: 'Conversations' })).getAllByRole('listitem')).toHaveLength(1)

    await userEvent.click(screen.getByRole('button', { name: 'Ordered' }))
    await waitFor(() => expect(sent.map((request) => request.path)).toContain('/summaries?outcome=submitted'))
    const rows = await screen.findAllByRole('listitem')
    await waitFor(() => expect(rows[0]).toHaveTextContent('ordered'))
    expect(screen.getByRole('button', { name: 'Ordered' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('opens the facts behind a note, and has it written again', async () => {
    const sent = installBackend()
    open()
    const list = await screen.findByRole('list', { name: 'Conversations' })
    const ordered = within(list).getAllByRole('listitem')[1]

    await userEvent.click(within(ordered).getByRole('button', { name: 'Details' }))

    expect(await within(ordered).findByText('2 × Cheeseburger, 1 × Fries')).toBeInTheDocument()
    expect(ordered).toHaveTextContent('1 min 35 s, 3 things said by the customer')
    expect(ordered).toHaveTextContent('oat milk shake')
    expect(ordered).toHaveTextContent('4 calls · 12,000 tokens in, 200 out')

    await userEvent.click(within(ordered).getByRole('button', { name: 'Write it again' }))

    await waitFor(() =>
      expect(sent).toContainEqual({ method: 'POST', path: `/sessions/${ORDERED.session_id}/summary` }),
    )
  })

  it('says when there are no notes yet', async () => {
    installBackend([])
    open()

    expect(await screen.findByText(/No notes yet/)).toBeInTheDocument()
  })

  it('is linked from the staff screen', async () => {
    installBackend()
    window.history.replaceState(null, '', '/staff')
    render(<App />)

    await userEvent.click(screen.getByRole('link', { name: 'Conversations' }))

    expect(window.location.pathname).toBe('/conversations')
  })
})
