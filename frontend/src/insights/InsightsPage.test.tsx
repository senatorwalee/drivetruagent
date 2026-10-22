/** The insights page against a stand-in backend. */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'
import type { UnmetRequest } from '../api/client'

const ROWS: UnmetRequest[] = [
  { request: 'oat milk shake', times: 41, match: 'close', last_asked: new Date(Date.now() - 15 * 60_000).toISOString() },
  { request: 'gluten-free bun', times: 27, match: 'none', last_asked: new Date(Date.now() - 3 * 3_600_000).toISOString() },
]

function installBackend(rowsByDays: Record<number, UnmetRequest[]> | 'down') {
  const asked: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      asked.push(url)
      if (rowsByDays === 'down') return Promise.reject(new TypeError('Failed to fetch'))
      const days = Number(new URL(url, 'http://test').searchParams.get('days'))
      const body = url.startsWith('/api/insights/unmet-requests') ? (rowsByDays[days] ?? []) : []
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    }),
  )
  return asked
}

function open(path = '/insights') {
  window.history.replaceState(null, '', path)
  render(<App />)
}

afterEach(() => vi.unstubAllGlobals())

describe('the insights page', () => {
  it('ranks what guests asked for, with how often and how close the menu came', async () => {
    installBackend({ 7: ROWS })
    open()

    const list = await screen.findByRole('list', { name: 'Requests, most asked first' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('oat milk shake')
    expect(items[0]).toHaveTextContent('41 times')
    expect(items[0]).toHaveTextContent('close match')
    expect(items[0]).toHaveTextContent('last asked 15 min ago')
    expect(items[1]).toHaveTextContent('not on the menu')
  })

  it('suggests what to do about the most-asked request', async () => {
    installBackend({ 7: ROWS })
    open()

    expect(await screen.findByText(/Guests asked for "oat milk shake" 41 times/)).toBeInTheDocument()
  })

  it('says what will appear when nothing has been asked yet', async () => {
    installBackend({ 7: [] })
    open()

    expect(await screen.findByText(/Nothing yet for the last 7 days/)).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Requests, most asked first' })).not.toBeInTheDocument()
  })

  it('asks the backend for the period chosen', async () => {
    const asked = installBackend({ 7: [], 30: ROWS })
    open()
    await screen.findByText(/Nothing yet/)

    await userEvent.click(screen.getByRole('radio', { name: 'Last 30 days' }))

    expect(await screen.findByText('oat milk shake')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Most asked, last 30 days' })).toBeInTheDocument()
    expect(asked).toContain('/api/insights/unmet-requests?days=30')
  })

  it('says when the backend cannot be reached', async () => {
    installBackend('down')
    open()

    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach the backend')
  })

  it('is reached from the staff screen', async () => {
    installBackend({ 7: ROWS })
    open('/staff')

    await userEvent.click(screen.getByRole('link', { name: 'Insights' }))

    await waitFor(() => expect(window.location.pathname).toBe('/insights'))
    expect(await screen.findByRole('heading', { name: 'Asked for, not on the menu' })).toBeInTheDocument()
  })
})
