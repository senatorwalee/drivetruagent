/** The home page against a stand-in backend. */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'
import type { AdminItem, ConversationSummary, HandoffSummary, UnmetRequest } from '../api/client'
import { agentInfo, week } from '../test/fixtures'

const handoff = (status: HandoffSummary['status']): HandoffSummary => ({
  handoff_id: `h-${status}`,
  session_id: 's',
  lane: '1',
  language: 'en',
  status,
  reason: { code: 'human_requested', title: 'Asked for a person' },
  created_at: new Date().toISOString(),
  acknowledged_by: null,
  item_count: 2,
  total_cents: 900,
})

const requests: UnmetRequest[] = [{ request: 'oat milk shake', times: 41, match: 'close', last_asked: new Date().toISOString() }]

const note = (followUp: string | null) => ({ session_id: `s-${followUp ?? 'none'}`, follow_up: followUp }) as ConversationSummary

const menuItem = (code: string, hasPhoto: boolean, active = true) =>
  ({ item_code: code, is_active: active, photo: hasPhoto ? { version: 'v', credit: null } : null }) as AdminItem

function installBackend(reachable = true) {
  const answers: Record<string, unknown> = {
    '/agent': agentInfo,
    '/specials/week': week,
    '/handoffs': [handoff('open'), handoff('acknowledged'), handoff('resolved')],
    '/insights/unmet-requests': requests,
    '/summaries': [note('Call back about the sesame question.'), note(null)],
    '/admin/menu': { categories: [], items: [menuItem('fries', true), menuItem('cola', false), menuItem('pie', false, false)] },
  }
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      if (!reachable) return Promise.reject(new TypeError('Failed to fetch'))
      const path = String(input).replace(/^\/api/, '').split('?')[0]
      const known = path in answers
      const body = known ? answers[path] : { error: { code: 'not_found', message: 'Not here', details: {} } }
      return Promise.resolve(new Response(JSON.stringify(body), { status: known ? 200 : 404, headers: { 'Content-Type': 'application/json' } }))
    }),
  )
}

function open(path = '/?design=current') {
  window.history.replaceState(null, '', path)
  render(<App />)
}

afterEach(() => vi.unstubAllGlobals())

describe('the home page', () => {
  it('says what Frontdesk is, for which restaurant, and which model answers', async () => {
    installBackend()
    open()

    expect(await screen.findByText('Maple Lane Burgers · drive-through and phone')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('The agent takes the order.')
    expect(screen.getByText('Scripted demo: no API key needed')).toBeInTheDocument()
  })

  it('gives each part of the app a card with a live number', async () => {
    installBackend()
    open()

    const cards = screen.getByRole('list', { name: 'Parts of Frontdesk' })
    const live = within(cards).getByRole('link', { name: /Live lane/ })
    const staff = within(cards).getByRole('link', { name: /Staff/ })
    const insights = within(cards).getByRole('link', { name: /Insights/ })
    const menu = within(cards).getByRole('link', { name: /Menu/ })

    // Wednesday is today in the fixture: onion rings, 3.29 less 1.00.
    expect(await within(live).findByText('$2.29')).toBeInTheDocument()
    expect(live).toHaveTextContent('Onion rings today, usually $3.29')
    // Open and picked-up handoffs are waiting; a resolved one is not.
    expect(await within(staff).findByText('2')).toBeInTheDocument()
    expect(await within(insights).findByText('“oat milk shake”')).toBeInTheDocument()
    expect(insights).toHaveTextContent('41 times')
    // Items taken off the menu are not counted.
    expect(await within(menu).findByText('2')).toBeInTheDocument()
    expect(menu).toHaveTextContent('1 without a photo')
    expect(menu).toHaveAttribute('href', '/menu')
    const conversations = within(cards).getByRole('link', { name: /Conversations/ })
    expect(await within(conversations).findByText('1')).toBeInTheDocument()
    expect(conversations).toHaveTextContent('note to follow up')
  })

  it('opens the live lane, which now lives at /live', async () => {
    installBackend()
    open()

    await userEvent.click(screen.getByRole('link', { name: /Open the live lane/ }))

    expect(window.location.pathname).toBe('/live')
  })

  it('is reached from the Frontdesk wordmark on every other page', async () => {
    installBackend()
    open('/insights')

    await userEvent.click(screen.getByRole('link', { name: 'Frontdesk' }))

    expect(window.location.pathname).toBe('/')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Keep orders moving.')
  })

  it('is where an unknown address lands', () => {
    installBackend()
    open('/nowhere')

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Keep orders moving.')
  })

  it('says so when the backend cannot be reached, and the cards still lead somewhere', async () => {
    installBackend(false)
    open()

    expect(await screen.findByRole('alert')).toHaveTextContent(/Cannot reach the backend/)
    expect(screen.getAllByText('not available').length).toBe(5)
    expect(screen.getByRole('link', { name: /Staff/ })).toHaveAttribute('href', '/staff')
  })
})

describe('the home page designs', () => {
  it('adds the business option, keeps its scenario controls working, and returns to either original design', async () => {
    installBackend()
    open('/?design=business')

    expect(screen.getByRole('button', { name: 'Business' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Keep orders moving.')
    expect(screen.getAllByRole('link', { name: 'Try an order' }).every((link) => link.getAttribute('href') === '/live')).toBe(true)
    expect(screen.getByRole('link', { name: 'Workspace' })).toHaveAttribute('href', '/staff')
    await userEvent.click(screen.getByRole('button', { name: 'Staff handoffs' }))
    expect(screen.getByRole('link', { name: 'Explore staff handoffs' })).toHaveAttribute('href', '/staff')
    expect(screen.getByText('I’d like to speak with someone about my order.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'New' }))
    expect(screen.getByRole('heading', { name: /in three steps/ })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Business' }))
    expect(window.location.search).toBe('?design=business')
    expect(window.localStorage.getItem('drivetruagent.home-design')).toBe('business')
    await userEvent.click(screen.getByRole('button', { name: 'Current' }))
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('The agent takes the order.')
    expect(screen.getByRole('list', { name: 'Parts of Frontdesk' })).toBeInTheDocument()
  })

  it('opens on Business for a new visitor', () => {
    installBackend()
    open('/')

    expect(screen.getByRole('button', { name: 'Business' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Keep orders moving.')
    expect(screen.queryByRole('heading', { name: /in three steps/ })).not.toBeInTheDocument()
    expect(screen.queryByText('Voice AI · Drive-through and phone orders')).not.toBeInTheDocument()
  })

  it('honors an existing saved homepage choice', () => {
    installBackend()
    window.localStorage.setItem('drivetruagent.home-design', 'current')
    open('/')

    expect(screen.getByRole('button', { name: 'Current' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('The agent takes the order.')
  })

  it('switches to the new design, which tells the story around the cards', async () => {
    installBackend()
    open()

    await userEvent.click(screen.getByRole('button', { name: 'New' }))

    expect(screen.getByRole('button', { name: 'New' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Voice AI · Drive-through and phone orders')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('The agent takes the order.The backend checks every step.')
    const steps = within(screen.getByRole('region', { name: /in three steps/ })).getAllByRole('listitem')
    expect(steps.map((step) => within(step).getByRole('heading').textContent)).toEqual([
      'A guest arrives',
      'The agent takes the order',
      'The code checks every step',
    ])
    expect(screen.getByRole('region', { name: /So the cook can stay in the kitchen/ })).toBeInTheDocument()
    expect(screen.getByText('What happens when it mishears?')).toBeInTheDocument()
    // The cards are still there.
    expect(screen.getByRole('list', { name: 'Parts of Frontdesk' })).toBeInTheDocument()
  })

  it('remembers the choice, and switches back', async () => {
    installBackend()
    open()
    await userEvent.click(screen.getByRole('button', { name: 'New' }))

    expect(window.location.search).toBe('?design=new')
    expect(window.localStorage.getItem('drivetruagent.home-design')).toBe('new')

    await userEvent.click(screen.getByRole('button', { name: 'Current' }))

    expect(screen.queryByRole('heading', { name: /in three steps/ })).not.toBeInTheDocument()
    expect(window.localStorage.getItem('drivetruagent.home-design')).toBe('current')
  })

  it('opens the design the address names', () => {
    installBackend()
    open('/?design=new')

    expect(screen.getByRole('button', { name: 'New' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('heading', { name: /in three steps/ })).toBeInTheDocument()
  })
})
