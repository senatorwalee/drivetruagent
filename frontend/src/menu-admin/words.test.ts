import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import type { AdminItem } from '../api/client'
import { changesFrom, checkForm, emptyForm, explainError, formFromItem, optionsLabel, parsePrice, parseStock, stockFrom, stockLabel } from './words'

const FRIES: AdminItem = {
  item_code: 'fries',
  category_code: 'sides',
  name_en: 'Fries',
  name_fr: 'Frites',
  description_en: null,
  description_fr: null,
  base_price_cents: 249,
  is_active: true,
  stock: 100,
  sold_out: false,
  photo: { version: 'abc', credit: 'Mitchell Luo on Unsplash' },
  allergens_reviewed_on: '2026-10-01',
  choices: [
    {
      group_name: 'Size',
      required: true,
      options: [
        { option_name: 'Small', price_delta_cents: 0 },
        { option_name: 'Large', price_delta_cents: 130 },
      ],
    },
  ],
}

describe('reading the form', () => {
  it.each([
    ['5', 500],
    ['5.4', 540],
    ['5.49', 549],
    [' $6.90 ', 690],
    ['0', 0],
  ])('reads %j as %i cents', (text, cents) => {
    expect(parsePrice(text)).toBe(cents)
  })

  it.each(['', 'abc', '5.499', '-1', '5,49', '12345'])('does not take %j as a price', (text) => {
    expect(parsePrice(text)).toBeNull()
  })

  it('reads blank stock as not tracked, and refuses what is not a whole number', () => {
    expect(parseStock(' ')).toBeNull()
    expect(parseStock('12')).toBe(12)
    expect(parseStock('1.5')).toBeUndefined()
    expect(parseStock('-3')).toBeUndefined()
  })

  it('says what is wrong, field by field', () => {
    const problems = checkForm({ ...emptyForm('sides'), price: 'free', stockMode: 'counted', stock: 'lots', descriptionFr: 'Croustillant' })

    expect(Object.keys(problems).sort()).toEqual(['descriptionEn', 'nameEn', 'price', 'stock'])
  })

  it('finds nothing wrong with a filled form', () => {
    expect(checkForm(formFromItem(FRIES))).toEqual({})
  })
})

describe('what is sent', () => {
  it('sends only what changed, and unlimited as no stock at all', () => {
    const form = { ...formFromItem(FRIES), price: '2.99', stockMode: 'unlimited' as const, nameFr: '' }

    expect(changesFrom(form, FRIES)).toEqual({ base_price_cents: 299, stock: null, name_fr: null })
  })

  it('sends nothing when the form is as saved', () => {
    expect(changesFrom(formFromItem(FRIES), FRIES)).toEqual({})
  })
})

describe('words for an item', () => {
  it('reads the quantity choice as what to send', () => {
    expect(stockFrom({ stockMode: 'unlimited', stock: '40' })).toBeNull()
    expect(stockFrom({ stockMode: 'sold_out', stock: '40' })).toBe(0)
    expect(stockFrom({ stockMode: 'counted', stock: '40' })).toBe(40)
    expect(stockFrom({ stockMode: 'counted', stock: ' ' })).toBeUndefined()
    expect(stockFrom({ stockMode: 'counted', stock: 'lots' })).toBeUndefined()
  })

  it('opens an item on the quantity choice it has now', () => {
    expect(formFromItem(FRIES)).toMatchObject({ stockMode: 'counted', stock: '100' })
    expect(formFromItem({ ...FRIES, stock: null })).toMatchObject({ stockMode: 'unlimited', stock: '' })
    expect(formFromItem({ ...FRIES, stock: 0 })).toMatchObject({ stockMode: 'sold_out', stock: '' })
  })

  it('describes stock in plain words', () => {
    expect(stockLabel(FRIES)).toBe('100 in stock')
    expect(stockLabel({ ...FRIES, stock: 0, sold_out: true })).toBe('Sold out')
    expect(stockLabel({ ...FRIES, stock: null })).toBe('Unlimited')
  })

  it('lists choices with what they add to the price', () => {
    expect(optionsLabel(FRIES.choices[0])).toBe('Small, Large +$1.30')
  })

  it('turns a refused name into a message on the name field', () => {
    const error = new ApiError(409, 'duplicate_item_name', "'fries' is already called 'Fries'.")

    expect(explainError(error, formFromItem(FRIES))).toEqual({
      field: 'nameEn',
      message: 'Another item on the menu is already called “Fries”.',
    })
  })

  it('says the backend is unreachable when the request never got there', () => {
    expect(explainError(new TypeError('Failed to fetch'), formFromItem(FRIES)).message).toMatch(/Could not reach the backend/)
  })
})
