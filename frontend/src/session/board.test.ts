import { describe, expect, it } from 'vitest'

import type { Cart, Combo, Menu } from '../api/client'
import { stockLevels, week } from '../test/fixtures'
import { comboStates, isLow, stockGroups, todaysSpecials } from './board'

const item = (item_code: string, item_name: string) => ({
  item_code,
  item_name,
  base_price_cents: 100,
  must_ask: [],
  in_stock: true,
})

const menu: Menu = {
  categories: [
    { category_code: 'sides', category_name: 'Sides', items: [item('fries', 'Fries'), item('onion_rings', 'Onion rings')] },
    { category_code: 'drinks', category_name: 'Drinks', items: [item('cola', 'Cola'), item('bottled_water', 'Bottled water')] },
    { category_code: 'burgers', category_name: 'Burgers', items: [item('cheeseburger', 'Cheeseburger')] },
  ],
}

describe('stockGroups', () => {
  it('lists stock by category in menu order, with what is held in carts', () => {
    const groups = stockGroups(menu, stockLevels, week)

    expect(groups.map((group) => group.categoryName)).toEqual(['Sides', 'Drinks'])
    expect(groups[0]?.rows).toEqual([
      { itemCode: 'fries', itemName: 'Fries', onHand: 100, held: 1, available: 99, special: false },
      { itemCode: 'onion_rings', itemName: 'Onion rings', onHand: 40, held: 0, available: 40, special: true },
    ])
  })

  it('leaves out items whose stock is not tracked, and categories left empty', () => {
    const groups = stockGroups(menu, stockLevels, week)

    expect(groups.flatMap((group) => group.rows.map((row) => row.itemCode))).not.toContain('bottled_water')
    expect(groups.map((group) => group.categoryName)).not.toContain('Burgers')
  })

  it("marks today's special and only today's", () => {
    const specials = stockGroups(menu, stockLevels, week).flatMap((group) => group.rows.filter((row) => row.special))

    expect(specials.map((row) => row.itemCode)).toEqual(['onion_rings'])
  })

  it('marks nothing as special before the week has loaded', () => {
    const rows = stockGroups(menu, stockLevels, null).flatMap((group) => group.rows)

    expect(rows.some((row) => row.special)).toBe(false)
  })

  it('is empty until the menu has loaded', () => {
    expect(stockGroups(undefined, stockLevels, week)).toEqual([])
  })
})

describe('todaysSpecials', () => {
  it("is today's from the week", () => {
    expect(todaysSpecials(week).map((special) => special.item_code)).toEqual(['onion_rings'])
  })

  it('is empty with no week', () => {
    expect(todaysSpecials(null)).toEqual([])
  })
})

describe('isLow', () => {
  it('is true at five or fewer', () => {
    expect([6, 5, 0].map((available) => isLow({ available }))).toEqual([false, true, true])
  })
})

describe('comboStates', () => {
  const combo: Combo = {
    code: 'burger_combo',
    name: 'Burger combo',
    saves_cents: 200,
    slots: [
      { category_code: 'burgers', category_name: 'Burgers' },
      { category_code: 'sides', category_name: 'Sides' },
      { category_code: 'drinks', category_name: 'Drinks' },
    ],
  }
  const line = (line_no: number, item_code: string, item_name: string, quantity = 1) => ({
    line_no,
    item_code,
    item_name,
    quantity,
    options: [],
    unit_price_cents: 100,
    line_total_cents: 100 * quantity,
  })
  const cartOf = (lines: Cart['lines'], discounts: Cart['discounts'] = []): Cart => ({
    session_id: 's',
    state: 'ordering',
    language: 'en',
    version: 1,
    item_count: lines.length,
    subtotal_cents: 0,
    discounts,
    discount_cents: 0,
    tax_cents: 0,
    total_cents: 0,
    lines,
  })

  it('fills each slot with an item of its category from the cart', () => {
    const [state] = comboStates([combo], menu, cartOf([line(1, 'cola', 'Cola'), line(2, 'cheeseburger', 'Cheeseburger')]))

    expect(state?.slots).toEqual([
      { categoryName: 'Burgers', itemName: 'Cheeseburger' },
      { categoryName: 'Sides', itemName: null },
      { categoryName: 'Drinks', itemName: 'Cola' },
    ])
    expect(state?.times).toBe(0)
  })

  it('takes whether it applies from the cart the backend priced, never from the slots', () => {
    const full = [line(1, 'cheeseburger', 'Cheeseburger'), line(2, 'fries', 'Fries'), line(3, 'cola', 'Cola')]
    const priced = [{ kind: 'combo' as const, code: 'burger_combo', name: 'Burger combo', times: 1, amount_cents: 200 }]

    expect(comboStates([combo], menu, cartOf(full))[0]?.times).toBe(0)
    expect(comboStates([combo], menu, cartOf(full, priced))[0]?.times).toBe(1)
  })

  it('counts each unit once, so a slot needing two takes two', () => {
    const twoDrinks = { ...combo, slots: [...combo.slots, { category_code: 'drinks', category_name: 'Drinks' }] }

    const [state] = comboStates([twoDrinks], menu, cartOf([line(1, 'cola', 'Cola', 2)]))

    expect(state?.slots.map((slot) => slot.itemName)).toEqual([null, null, 'Cola', 'Cola'])
  })

  it('shows empty slots with no cart or menu yet', () => {
    expect(comboStates([combo], undefined, null)[0]?.slots.every((slot) => slot.itemName === null)).toBe(true)
  })
})
