/**
 * The same checks run against all three views. They look different, but each
 * must show the same session and offer the same controls.
 */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentType } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { buildTimeline } from '../session/timeline'
import { cart, comboCart, liveSession, replied, resetSeq, session, timer, toolRound } from '../test/fixtures'
import { ControlRoom } from './control-room/ControlRoom'
import { MenuBoard } from './menu-board/MenuBoard'
import { Receipt } from './receipt/Receipt'
import type { LiveSessionViewProps, ViewId } from './shared/viewTypes'

const VIEWS: [ViewId, ComponentType<LiveSessionViewProps>][] = [
  ['menu-board', MenuBoard],
  ['receipt', Receipt],
  ['control-room', ControlRoom],
]

describe.each(VIEWS)('%s view', (viewId, View) => {
  const show = (overrides: Parameters<typeof liveSession>[0] = {}, onViewChange = vi.fn()) => {
    const live = liveSession(overrides)
    render(<View live={live} view={viewId} onViewChange={onViewChange} />)
    return { live, onViewChange, order: screen.getByRole('complementary', { name: 'Order' }) }
  }

  it('names the restaurant', () => {
    show()

    expect(screen.getByText('Maple Lane Burgers')).toBeInTheDocument()
  })

  it('shows what the agent is doing, in words', () => {
    show({ agentState: 'listening' })

    expect(screen.getByRole('img', { name: 'Agent: Listening' })).toBeInTheDocument()
    expect(screen.getByText('Listening')).toBeInTheDocument()
  })

  it('shows the latest thing the agent said', () => {
    show()

    expect(screen.getByText('Fifteen cookies, is that right?')).toBeInTheDocument()
  })

  it('lists the cart lines with their options and the total', () => {
    const { order } = show()

    expect(within(order).getByText(/1 (× )?Fries/)).toBeInTheDocument()
    expect(within(order).getByText(/Large . Regular/)).toBeInTheDocument()
    expect(within(order).getByText(/1 (× )?Cola/)).toBeInTheDocument()
    expect(within(order).getByText('$6.98')).toBeInTheDocument()
  })

  it('shows a combo saving between the subtotal and the total', () => {
    const { order } = show({ cart: comboCart })

    expect(within(order).getByText('Burger combo')).toBeInTheDocument()
    expect(within(order).getByText(/^−\$?2\.00$/)).toBeInTheDocument()
    expect(within(order).getByText('$11.49')).toBeInTheDocument()
  })

  it('says how many times a saving applied when it is more than once', () => {
    const twice = [{ ...comboCart.discounts[0]!, times: 2, amount_cents: 400 }]
    const { order } = show({ cart: { ...comboCart, discounts: twice, discount_cents: 400 } })

    expect(within(order).getByText('Burger combo × 2')).toBeInTheDocument()
    expect(within(order).getByText(/^−\$?4\.00$/)).toBeInTheDocument()
  })

  it('shows no saving row on an order that earned none', () => {
    const { order } = show()

    expect(within(order).queryByText(/combo/i)).not.toBeInTheDocument()
    expect(within(order).queryByText(/−/)).not.toBeInTheDocument()
  })

  it('shows when the backend suggested offering a combo', () => {
    resetSeq()
    const suggestion = { kind: 'combo', code: 'burger_combo', name: 'Burger combo', saves_cents: 200, missing: [] }
    const timeline = buildTimeline(
      toolRound('add_item', { item_code: 'cheeseburger', quantity: 1, selections: [] }, { decision: 'allow', suggestion, cart: { version: 1 } }),
    )
    show({ timeline })

    expect(screen.getByText(/suggests burger combo/)).toBeInTheDocument()
  })

  it('shows a held item apart from the cart, by name', () => {
    const { order } = show()

    expect(within(order).getByText(/15 (× )?Chocolate chip cookie/)).toBeInTheDocument()
    expect(within(order).getByText(/not (added yet|in the cart)/i)).toBeInTheDocument()
  })

  it('shows each decision with its name', () => {
    show()

    expect(screen.getAllByText('allow').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('confirm').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/cookie × 15/)).toBeInTheDocument()
  })

  it('says the order was placed, with its number', () => {
    resetSeq()
    const placed = buildTimeline(toolRound('submit_order', {}, { decision: 'allow', order: { order_number: 12, total_cents: 698 } }))
    const { order } = show({ timeline: placed, agentState: 'ended', session: { ...session, state: 'submitted' } })

    expect(within(order).getByText(/order 12/i)).toBeInTheDocument()
  })

  it('has an empty state before anything is ordered', () => {
    show({ cart: { ...cart, lines: [], item_count: 0, subtotal_cents: 0, tax_cents: 0, total_cents: 0 }, timeline: buildTimeline([]) })

    expect(screen.getByText(/say hello to start/i)).toBeInTheDocument()
    expect(screen.getAllByText('$0.00').length).toBeGreaterThanOrEqual(1)
  })

  it('sends what is typed and clears the box', async () => {
    const send = vi.fn()
    show({ send })
    const box = screen.getByLabelText('What the customer says')

    await userEvent.type(box, 'No, just two.')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(send).toHaveBeenCalledWith('No, just two.')
    expect(box).toHaveValue('')
  })

  it('tells the indicator when the customer starts and stops typing', async () => {
    const setCustomerActive = vi.fn()
    show({ setCustomerActive })

    await userEvent.type(screen.getByLabelText('What the customer says'), 'Hi')

    expect(setCustomerActive).toHaveBeenLastCalledWith(true)
  })

  it('offers the next line of the demo script, which fills the box', async () => {
    show()

    await userEvent.click(screen.getByRole('button', { name: /No, just two\./ }))

    expect(screen.getByLabelText('What the customer says')).toHaveValue('No, just two.')
  })

  it('does not offer a script line with a real model', () => {
    show({ suggestedLine: null })

    expect(screen.queryByText(/demo script/i)).not.toBeInTheDocument()
  })

  it('cannot send while the agent is busy or the session is over', () => {
    show({ canSend: false })

    expect(screen.getByLabelText('What the customer says')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('shows what the backend timer is counting down to', () => {
    show({ timerNote: 'hand-off in 2:18' })

    expect(screen.getByText(/hand-off in 2:18/)).toBeInTheDocument()
  })

  it('shows nothing about timers when none is running', () => {
    show({ timerNote: null })

    expect(screen.queryByText(/hand-off in|closes in|timers paused/)).not.toBeInTheDocument()
  })

  it('shows when the clock, not the model, did something', () => {
    resetSeq()
    const timeline = buildTimeline([timer('nudge', 'silence', 20), replied('Are you still there?')])
    show({ timeline })

    expect(screen.getAllByText('clock').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/asked after 20s of silence/)).toBeInTheDocument()
    expect(screen.getByText('Are you still there?')).toBeInTheDocument()
  })

  it('pauses and resumes', async () => {
    const togglePause = vi.fn()
    show({ togglePause })

    await userEvent.click(screen.getByRole('button', { name: 'Pause' }))

    expect(togglePause).toHaveBeenCalledOnce()
  })

  it('shows Resume while paused', () => {
    show({ paused: true, agentState: 'paused' })

    expect(screen.getByRole('button', { name: 'Resume' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('starts a new session', async () => {
    const startNewSession = vi.fn()
    show({ startNewSession })

    await userEvent.click(screen.getByRole('button', { name: 'New session' }))

    expect(startNewSession).toHaveBeenCalledOnce()
  })

  it('reports a problem where a screen reader will announce it', () => {
    show({ problem: 'Cannot reach the backend.' })

    expect(screen.getByRole('alert')).toHaveTextContent('Cannot reach the backend.')
  })

  it('marks itself as the current view and can switch to another', async () => {
    const { onViewChange } = show()
    const switcher = screen.getByRole('group', { name: 'View' })
    const other = viewId === 'receipt' ? 'Control room' : 'Receipt'

    expect(within(switcher).getAllByRole('button', { pressed: true })).toHaveLength(1)
    await userEvent.click(within(switcher).getByRole('button', { name: other }))

    expect(onViewChange).toHaveBeenCalledWith(other === 'Receipt' ? 'receipt' : 'control-room')
  })
})
