/** The staff screen against a stand-in backend: read the queue, pick one up, close it. */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { App } from '../App'
import type { HandoffPackage } from '../api/client'
import { installFakeStaffBackend } from '../test/fakeStaffBackend'
import { cancelledEarlier, largeOrder, timedOut } from '../test/handoffFixtures'

function open(handoffs: HandoffPackage[] = [largeOrder, timedOut, cancelledEarlier]) {
  window.history.replaceState(null, '', '/staff')
  window.localStorage.clear()
  const backend = installFakeStaffBackend(handoffs)
  render(<App />)
  return backend
}

const queue = () => screen.getByRole('navigation', { name: 'Handoffs' })
const shown = () => screen.findByRole('article', { name: 'Handoff package' })

beforeEach(() => window.history.replaceState(null, '', '/staff'))

describe('the staff screen', () => {
  it('lists who is waiting, why, and what they had ordered', async () => {
    open()

    const first = await within(queue()).findByRole('button', { name: /Very large order/ })
    expect(first).toHaveTextContent('waiting')
    expect(first).toHaveTextContent('lane-1 · 2 items · $6.98')
    expect(within(queue()).getByRole('button', { name: /Took longer than the time limit/ })).toHaveTextContent('lane-2 · 0 items · $0.00')
    expect(within(queue()).getByRole('button', { name: /Waiting/ })).toHaveTextContent('2')
    // Closed handoffs are not mixed in with the ones that need someone.
    expect(within(queue()).queryByText(/lane-3/)).not.toBeInTheDocument()
  })

  it('opens the longest-waiting handoff and says what to do about it', async () => {
    open()

    const pack = await shown()
    expect(within(pack).getByRole('heading', { name: 'Very large order' })).toBeInTheDocument()
    expect(within(pack).getByText(/asked for 400 of Chocolate chip cookie and confirmed it/)).toBeInTheDocument()
  })

  it('shows the cart, the stock it holds, the conversation and what the backend decided', async () => {
    open()
    const pack = await shown()

    const cart = within(pack).getByRole('region', { name: 'Cart' })
    // Once as a line of the cart, once as stock being held for it.
    expect(within(cart).getAllByText(/1 × Fries/)).toHaveLength(2)
    expect(within(cart).getByText('Large · Regular')).toBeInTheDocument()
    expect(within(cart).getAllByText('$6.98').length).toBeGreaterThanOrEqual(1)
    expect(within(cart).getAllByText(/^held until/)).toHaveLength(2)

    const conversation = within(pack).getByRole('region', { name: 'Conversation' })
    expect(within(conversation).getByText('Four hundred cookies, is that right?')).toBeInTheDocument()
    expect(within(conversation).getByText('6 earlier lines not shown.')).toBeInTheDocument()

    const steps = within(pack).getByRole('region', { name: 'What the backend decided' })
    expect(within(steps).getByText('cookie × 400 · extreme quantity')).toBeInTheDocument()
    expect(within(steps).getByText('confirm')).toBeInTheDocument()
    expect(within(steps).getByText('escalate')).toBeInTheDocument()
  })

  it('shows another handoff when it is chosen', async () => {
    open()
    await shown()

    await userEvent.click(within(queue()).getByRole('button', { name: /Took longer than the time limit/ }))

    const pack = await screen.findByRole('article', { name: 'Handoff package' })
    await waitFor(() => expect(within(pack).getByRole('heading', { name: 'Took longer than the time limit' })).toBeInTheDocument())
    expect(within(pack).getByText('hand off · time limit')).toBeInTheDocument()
    expect(within(pack).getByText('clock')).toBeInTheDocument()
  })

  it('lets someone say they have picked it up, by name', async () => {
    const backend = open()
    const pack = await shown()

    await userEvent.type(within(pack).getByLabelText('Your name'), 'Sam')
    await userEvent.click(within(pack).getByRole('button', { name: "I've got this one" }))

    await waitFor(() => expect(within(queue()).getByRole('button', { name: /Very large order/ })).toHaveTextContent('with Sam'))
    expect(backend.actions()).toEqual([{ method: 'POST', path: `/handoffs/${largeOrder.handoff_id}/acknowledge`, body: { by: 'Sam' } }])
    // Once someone has it, there is nothing left to claim.
    expect(within(await shown()).queryByRole('button', { name: "I've got this one" })).not.toBeInTheDocument()
  })

  it('places the order as it stands and shows its number', async () => {
    const backend = open()
    const pack = await shown()

    await userEvent.type(within(pack).getByLabelText('Note (optional)'), 'Two items only.')
    await userEvent.click(within(pack).getByRole('button', { name: /Place order as it stands · \$6\.98/ }))

    const outcome = await screen.findByRole('region', { name: 'Outcome' })
    expect(outcome).toHaveTextContent('Order 12 was placed. Total $6.98.')
    expect(outcome).toHaveTextContent('Note: Two items only.')
    expect(backend.actions()).toEqual([
      { method: 'POST', path: `/handoffs/${largeOrder.handoff_id}/resolve`, body: { outcome: 'order_placed', note: 'Two items only.' } },
    ])
    // It is finished: no more actions, and one fewer waiting.
    expect(screen.queryByRole('region', { name: 'Actions' })).not.toBeInTheDocument()
    await waitFor(() => expect(within(queue()).getByRole('button', { name: /Waiting/ })).toHaveTextContent('1'))
    // The screen stays on the order just placed. It does not jump to the next
    // handoff in the queue before the person has seen the order number.
    expect(within(await shown()).getByRole('heading', { name: 'Very large order' })).toBeInTheDocument()
  })

  it('cancels an order and says nothing was ordered', async () => {
    const backend = open()
    const pack = await shown()

    await userEvent.click(within(pack).getByRole('button', { name: 'Cancel order' }))

    expect(await screen.findByRole('region', { name: 'Outcome' })).toHaveTextContent('Cancelled. Nothing was ordered.')
    expect(backend.actions()[0]?.body).toEqual({ outcome: 'cancelled', note: null })
  })

  it('explains why an order could not be placed, and leaves it open', async () => {
    const backend = open()
    backend.refuseNextResolve('insufficient_stock')
    const pack = await shown()

    await userEvent.click(within(pack).getByRole('button', { name: /Place order as it stands/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/stock for this order has gone/)
    expect(screen.getByRole('region', { name: 'Actions' })).toBeInTheDocument()
    expect(within(queue()).getByRole('button', { name: /Very large order/ })).toHaveTextContent('waiting')
  })

  it('says so when someone else closed it first', async () => {
    const backend = open()
    backend.refuseNextResolve('handoff_resolved')
    const pack = await shown()

    await userEvent.click(within(pack).getByRole('button', { name: 'Cancel order' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Someone else has already closed this handoff.')
  })

  it('will not offer to place an empty cart', async () => {
    open([timedOut])
    const pack = await shown()

    expect(within(pack).getByRole('button', { name: /Place order as it stands/ })).toBeDisabled()
    expect(within(pack).getByText('The cart is empty, so there is no order to place.')).toBeInTheDocument()
    expect(within(pack).getByRole('button', { name: 'Cancel order' })).toBeEnabled()
  })

  it('keeps closed handoffs in their own list, with how they ended', async () => {
    open()
    await shown()

    await userEvent.click(within(queue()).getByRole('button', { name: 'Closed' }))

    const closed = await within(queue()).findByRole('button', { name: /lane-3/ })
    expect(closed).toHaveTextContent('closed')
    await userEvent.click(closed)
    const outcome = await screen.findByRole('region', { name: 'Outcome' })
    expect(outcome).toHaveTextContent('Cancelled. Nothing was ordered.')
    expect(outcome).toHaveTextContent('Note: Customer drove off.')
  })

  it('says so when nobody is waiting', async () => {
    open([])

    expect(await screen.findByText(/Nobody is waiting/)).toBeInTheDocument()
    expect(screen.getByText('Select a handoff to see what it needs.')).toBeInTheDocument()
  })

  it('remembers the name of the person using it', async () => {
    open()
    await userEvent.type(within(await shown()).getByLabelText('Your name'), 'Sam')

    expect(window.localStorage.getItem('drivetruagent.staffName')).toBe('Sam')
  })

  it('says when the backend cannot be reached', async () => {
    const backend = open()
    backend.goOffline()

    expect(await screen.findByRole('alert', {}, { timeout: 6000 })).toHaveTextContent(/Cannot reach the backend/)
  })
})

describe('moving between the two screens', () => {
  it('has a link back to the live session', async () => {
    open([])

    await userEvent.click(await screen.findByRole('link', { name: 'Live session' }))

    expect(window.location.pathname).toBe('/')
    expect(screen.queryByRole('heading', { name: /Staff/ })).not.toBeInTheDocument()
  })

  it('returns to the staff screen with the Back button', async () => {
    open([])
    await userEvent.click(await screen.findByRole('link', { name: 'Live session' }))

    window.history.back()

    expect(await screen.findByRole('heading', { name: /Staff/ })).toBeInTheDocument()
  })
})
