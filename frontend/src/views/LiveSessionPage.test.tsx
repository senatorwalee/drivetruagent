/** The whole page against a stand-in backend: open, talk, switch view, start again. */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { App } from '../App'
import { installFakeBackend } from '../test/fakeBackend'

let backend: ReturnType<typeof installFakeBackend>

beforeEach(() => {
  window.history.replaceState(null, '', '/')
  backend = installFakeBackend()
})

async function sayThroughThePage(text: string) {
  const box = await screen.findByLabelText('What the customer says')
  // The box is disabled until the session has opened.
  await waitFor(() => expect(box).toBeEnabled())
  await userEvent.type(box, text)
  await userEvent.click(screen.getByRole('button', { name: 'Send' }))
}

describe('the live session page', () => {
  it('opens a session and shows the restaurant', async () => {
    render(<App />)

    expect(await screen.findByText('Maple Lane Burgers')).toBeInTheDocument()
    await waitFor(() => expect(backend.sessionsOpened()).toBe(1))
  })

  it('plays a turn: the reply appears, the cart fills and the decision is logged', async () => {
    render(<App />)

    await sayThroughThePage('A cookie please')

    expect(await screen.findByText('One cookie. Anything else?')).toBeInTheDocument()
    const order = screen.getByRole('complementary', { name: 'Order' })
    expect(await within(order).findByText(/Chocolate chip cookie/)).toBeInTheDocument()
    expect(within(order).getByText('$1.68')).toBeInTheDocument()
    expect(await screen.findByText('allow')).toBeInTheDocument()
    expect(await screen.findByRole('img', { name: 'Agent: Speaking' })).toBeInTheDocument()
  })

  it('offers the first line of the demo script', async () => {
    render(<App />)

    expect(await screen.findByRole('button', { name: /Hi$/ })).toBeInTheDocument()
  })

  it('keeps the same session and conversation when the view is switched', async () => {
    render(<App />)
    await sayThroughThePage('A cookie please')
    await screen.findByText('One cookie. Anything else?')

    await userEvent.click(screen.getByRole('button', { name: 'Control room' }))

    expect(await screen.findByText('One cookie. Anything else?')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Decisions' })).toBeInTheDocument()
    expect(backend.sessionsOpened()).toBe(1)
  })

  it('remembers the chosen view in the address and for the next visit', async () => {
    render(<App />)

    await userEvent.click(await screen.findByRole('button', { name: 'Receipt' }))

    expect(window.location.search).toBe('?view=receipt')
    expect(window.localStorage.getItem('drivetruagent.view')).toBe('receipt')
    expect(screen.getByRole('heading', { name: 'System ledger' })).toBeInTheDocument()
  })

  it('opens in the view named in the address', async () => {
    window.history.replaceState(null, '', '/?view=control-room')

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Decisions' })).toBeInTheDocument()
  })

  it('resumes the same session after a reload', async () => {
    const first = render(<App />)
    await sayThroughThePage('A cookie please')
    await screen.findByText('One cookie. Anything else?')
    first.unmount()

    render(<App />)

    expect(await screen.findByText('One cookie. Anything else?')).toBeInTheDocument()
    expect(backend.sessionsOpened()).toBe(1)
  })

  it('pauses on the backend, because its timers are what must stop', async () => {
    render(<App />)
    await sayThroughThePage('A cookie')

    await userEvent.click(screen.getByRole('button', { name: 'Pause' }))

    expect(await screen.findByRole('button', { name: 'Resume' })).toHaveAttribute('aria-pressed', 'true')
    expect(backend.requests.filter((request) => request.path.endsWith('/pause'))).toHaveLength(1)
    expect(screen.getByText(/timers paused/)).toBeInTheDocument()
    expect(screen.getByLabelText('What the customer says')).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: 'Resume' }))

    expect(await screen.findByRole('button', { name: 'Pause' })).toBeInTheDocument()
    expect(backend.requests.filter((request) => request.path.endsWith('/resume'))).toHaveLength(1)
    expect(await screen.findByText(/hand-off in/)).toBeInTheDocument()
  })

  it('shows something the backend said by itself, without anyone typing', async () => {
    render(<App />)
    await sayThroughThePage('A cookie')
    expect(await screen.findByText('One cookie. Anything else?')).toBeInTheDocument()

    backend.nudge()

    expect(await screen.findByText('Are you still there?', {}, { timeout: 4000 })).toBeInTheDocument()
  })

  it('notices when the backend closes the session for silence', async () => {
    render(<App />)
    await sayThroughThePage('A cookie')
    await screen.findByText('One cookie. Anything else?')

    backend.abandon()

    expect(await screen.findByText('Abandoned', {}, { timeout: 4000 })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('What the customer says')).toBeDisabled())
    expect(screen.queryByText(/hand-off in/)).not.toBeInTheDocument()
  })

  it("shows this week's specials and the stock from the backend", async () => {
    render(<App />)

    const board = await screen.findByRole('region', { name: 'Specials and stock' })
    expect(await within(board).findByText('Onion rings', { selector: 'span' })).toBeInTheDocument()
    expect(backend.requests.some((request) => request.path === '/specials/week')).toBe(true)
    expect(backend.requests.some((request) => request.path === '/inventory')).toBe(true)
  })

  it('links to the staff screen', async () => {
    render(<App />)

    await userEvent.click(await screen.findByRole('link', { name: 'Staff' }))

    expect(window.location.pathname).toBe('/staff')
    expect(await screen.findByRole('heading', { name: /Staff/ })).toBeInTheDocument()
    expect(await screen.findByText(/Nobody is waiting/)).toBeInTheDocument()
  })

  it('starts a fresh session on request', async () => {
    render(<App />)
    await sayThroughThePage('A cookie please')
    await screen.findByText('One cookie. Anything else?')

    await userEvent.click(screen.getByRole('button', { name: 'New session' }))

    await waitFor(() => expect(backend.sessionsOpened()).toBe(2))
    await waitFor(() => expect(screen.queryByText('One cookie. Anything else?')).not.toBeInTheDocument())
    expect(await screen.findByText(/say hello to start/i)).toBeInTheDocument()
  })
})

describe('following phone calls', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/calls')
  })

  it('waits for a call, and opens no session of its own', async () => {
    render(<App />)

    expect(await screen.findByText(/No call in progress/)).toBeInTheDocument()
    expect(backend.sessionsOpened()).toBe(0)
  })

  it('shows the call as it happens, without a way to type into it', async () => {
    backend.ring()
    render(<App />)

    expect(await screen.findByText('Hi, thanks for calling Maple Lane Burgers.')).toBeInTheDocument()
    expect(screen.queryByText(/No call in progress/)).not.toBeInTheDocument()
    expect(await screen.findByLabelText('What the customer says')).toBeDisabled()
    expect(backend.sessionsOpened()).toBe(0)
  })
})
