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
