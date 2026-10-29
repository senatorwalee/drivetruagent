/**
 * The same checks run against all three views. They look different, but each
 * must show the same session and offer the same controls.
 */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentType } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { buildTimeline } from '../session/timeline'
import { cart, comboCart, liveSession, noVoice, replied, resetSeq, session, timer, toolRound } from '../test/fixtures'
import { ControlRoom } from './control-room/ControlRoom'
import { MenuBoard } from './menu-board/MenuBoard'
import { Receipt } from './receipt/Receipt'
import type { LiveSessionViewProps, ViewId } from './shared/viewTypes'

function comboState(times: number, burger: string | null, side: string | null, drink: string | null) {
  return {
    code: 'burger_combo',
    name: 'Burger combo',
    savesCents: 200,
    times,
    slots: [
      { categoryName: 'Burgers', itemName: burger },
      { categoryName: 'Sides', itemName: side },
      { categoryName: 'Drinks', itemName: drink },
    ],
  }
}

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

  it('shows the mic, disabled, when voice is not available', () => {
    show()

    expect(screen.getByRole('button', { name: 'Voice is not available' })).toBeDisabled()
  })

  it('starts a call from the mic', async () => {
    const start = vi.fn()
    show({ voice: { ...noVoice, available: true, start } })

    await userEvent.click(screen.getByRole('button', { name: 'Talk to the agent' }))

    expect(start).toHaveBeenCalledOnce()
  })

  it('hangs up from the same button during a call, and says to just speak', async () => {
    const stop = vi.fn()
    show({ voice: { ...noVoice, available: true, active: true, status: 'live', stop }, canSend: false })

    const hangUp = screen.getByRole('button', { name: 'Hang up' })
    expect(hangUp).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByPlaceholderText('On a call: just speak')).toBeDisabled()
    await userEvent.click(hangUp)

    expect(stop).toHaveBeenCalledOnce()
  })

  it('says the call is connecting while it is set up', () => {
    show({ voice: { ...noVoice, available: true, active: true, status: 'connecting' }, canSend: false })

    expect(screen.getByPlaceholderText('Connecting the call…')).toBeInTheDocument()
  })

  it("shows every day of the week's specials, with today marked", () => {
    show()

    const board = screen.getByRole('region', { name: 'Specials and stock' })
    const days = within(board).getAllByRole('listitem').filter((item) => /day/.test(item.textContent ?? ''))
    expect(days).toHaveLength(7)
    const today = days.find((day) => day.getAttribute('aria-current') === 'date')
    expect(today).toHaveTextContent('Wednesday')
    expect(today).toHaveTextContent('today')
    expect(today).toHaveTextContent('Onion rings')
    expect(today).toHaveTextContent('$2.29')
    expect(today).toHaveTextContent('$3.29')
  })

  it('shows the stock left, and what is held in carts', () => {
    show()

    const board = screen.getByRole('region', { name: 'Specials and stock' })
    expect(within(board).getAllByLabelText('99 available')).toHaveLength(2)
    expect(within(board).getAllByText('1 held')).toHaveLength(2)
    expect(within(board).getByLabelText('4 available')).toBeInTheDocument()
  })

  it('shows each combo with the cart items filling it, and what it still needs', () => {
    show({ board: { week: null, combos: [comboState(0, 'Classic burger', 'Fries', null)], stock: [] } })

    const card = screen.getByRole('listitem', { name: 'Burger combo' })
    expect(card).toHaveTextContent('save $2.00')
    expect(card).toHaveTextContent('Classic burger')
    expect(card).toHaveTextContent('Fries')
    expect(card).toHaveTextContent('Still needs: Drinks')
  })

  it('shows a combo the cart has earned, as the backend priced it', () => {
    show({ board: { week: null, combos: [comboState(2, 'Classic burger', 'Fries', 'Cola')], stock: [] } })

    expect(screen.getByRole('listitem', { name: 'Burger combo' })).toHaveTextContent('In this cart ×2 · −$4.00')
  })

  it('says when a special has sold out', () => {
    const soldOut = {
      ...liveSession().board.week!,
      days: liveSession().board.week!.days.map((day) => ({
        ...day,
        specials: day.specials.map((special) => ({ ...special, in_stock: !day.is_today })),
      })),
    }
    show({ board: { week: soldOut, combos: [], stock: [] } })

    expect(screen.getByText('sold out')).toBeInTheDocument()
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

describe('the control room', () => {
  const show = (overrides: Parameters<typeof liveSession>[0] = {}) =>
    render(<ControlRoom live={liveSession(overrides)} view="control-room" onViewChange={vi.fn()} />)

  it('shows what the session has cost in model calls so far', () => {
    show()

    expect(screen.getByText('Model cost').nextSibling).toHaveTextContent('0.27¢')
  })

  it('shows a dash until the cost is known', () => {
    show({ cost: null })

    expect(screen.getByText('Model cost').nextSibling).toHaveTextContent('—')
  })
})
