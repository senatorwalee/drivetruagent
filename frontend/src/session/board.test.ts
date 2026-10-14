import { describe, expect, it } from 'vitest'

import type { Menu } from '../api/client'
import { stockLevels, week } from '../test/fixtures'
import { isLow, stockGroups, todaysSpecials } from './board'

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
