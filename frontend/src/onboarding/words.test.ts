import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import type { AdminMenu, Combo } from '../api/client'
import { NONE_OF_THESE } from '../menu-admin/allergens'
import { emptyForm } from '../menu-admin/words'
import {
  allergenCounts,
  checkCombo,
  checkItem,
  cheapestSet,
  comboParts,
  dietCounts,
  draftItemFrom,
  formFromDraftItem,
  goLivePayload,
  goLiveProblems,
  goLiveRefusal,
  itemsBySection,
  loadDraft,
  menuToday,
  newDraft,
  priceLabel,
  reviewSection,
  saveDraft,
  sectionKey,
  sectionNames,
  setupSummary,
  stepProblem,
  withReview,
} from './words'
import type { Draft, DraftItem, MenuToday } from './words'
import { sampleDraft } from './sample'

const NOTHING_YET: MenuToday = { items: [], sections: [], comboNames: [] }
const MAPLE_LANE: MenuToday = {
  items: [
    { name: 'Classic burger', section: 'Burgers', cents: 549 },
    { name: 'Fries', section: 'Sides', cents: 249 },
    { name: 'Cola', section: 'Drinks', cents: 199 },
  ],
  sections: ['Burgers', 'Sides', 'Drinks'],
  comboNames: ['Burger combo'],
}

function item(name: string, section: string, cents: number, overrides: Partial<DraftItem> = {}): DraftItem {
  return {
    id: name,
    section,
    name_en: name,
    name_fr: null,
    description_en: null,
    description_fr: null,
    base_price_cents: cents,
    stock: null,
    diets: {},
    allergens: null,
    sizes: null,
    ...overrides,
  }
}

function draft(overrides: Partial<Draft> = {}): Draft {
  return { ...newDraft(), restaurant: { name: 'Harbour Grill', address: '', pickupMinutes: '15' }, ...overrides }
}

describe('the draft', () => {
  it('comes back as it was left', () => {
    const left = draft({ step: 'combos', items: [item('Taco', 'Tacos', 650)] })
    saveDraft(left)

    expect(loadDraft()).toEqual(left)
  })

  it('starts clean when there is none, or it is not readable', () => {
    expect(loadDraft()).toEqual(newDraft())

    window.localStorage.setItem('frontdesk.onboarding.draft', '{not json')
    expect(loadDraft()).toEqual(newDraft())

    window.localStorage.setItem('frontdesk.onboarding.draft', JSON.stringify({ version: 99 }))
    expect(loadDraft()).toEqual(newDraft())
  })

  it('keeps a draft saved before items had allergens, with nothing reviewed', () => {
    const { allergens: _none, sizes: _onePrice, ...older } = item('Fish taco', 'Mains', 650)
    const saved = { ...draft({ step: 'combos' }), version: 1, items: [older] }
    window.localStorage.setItem('frontdesk.onboarding.draft', JSON.stringify(saved))

    const loaded = loadDraft()

    expect(loaded.version).toBe(2)
    expect(loaded.step).toBe('combos')
    expect(loaded.items).toEqual([item('Fish taco', 'Mains', 650)])
  })
})

describe('the sample for a preview', () => {
  it('has nothing in the way of going live', () => {
    expect(goLiveProblems(sampleDraft(), NOTHING_YET)).toEqual([])
  })

  it('shows every state, with one item left not reviewed on purpose', () => {
    const sample = sampleDraft()

    expect(allergenCounts(sample)).toEqual({ reviewed: 5, unreviewed: 1 })
    expect(sample.items.some((each) => each.sizes !== null)).toBe(true)
    expect(sample.items.some((each) => each.stock !== null)).toBe(true)
    expect(sample.combos.length).toBeGreaterThan(0)
  })

  it('sends only what the backend would take, if it were ever sent', () => {
    const { items } = goLivePayload(sampleDraft())

    for (const each of items) {
      if (each.sizes) expect(each.base_price_cents).toBe(Math.min(...each.sizes.map((size) => size.price_cents)))
    }
  })

  it('is a fresh copy each time, so a reset undoes what was changed', () => {
    const first = sampleDraft()
    first.items.pop()

    expect(sampleDraft().items).toHaveLength(6)
  })
})

describe('allergens', () => {
  const mayContain = { noneOfThese: false, contains: [], mayContain: [{ allergen: 'sesame' as const, because: '' }] }

  it('counts an item as reviewed only once someone has said something', () => {
    const items = [item('A', 'Mains', 100), item('B', 'Mains', 100, { allergens: NONE_OF_THESE })]

    expect(allergenCounts(draft({ items }))).toEqual({ reviewed: 1, unreviewed: 1 })
  })

  it('lets an item go on not reviewed', () => {
    expect(stepProblem('allergens', draft({ items: [item('A', 'Mains', 100)] }), NOTHING_YET)).toBeNull()
  })

  it('stops at a may contain with no reason, at the step and at Go live', () => {
    const blocked = draft({ items: [item('Fries', 'Sides', 100, { allergens: mayContain })] })

    expect(stepProblem('allergens', blocked, NOTHING_YET)).toBe('Say why Fries may contain sesame.')
    expect(goLiveProblems(blocked, NOTHING_YET).map((each) => each.step)).toEqual(['allergens'])
  })

  it('sends null for a blank item and none of these only when it was chosen', () => {
    const payload = goLivePayload(draft({ items: [item('A', 'Mains', 100), item('B', 'Mains', 100, { allergens: NONE_OF_THESE })] }))

    expect(payload.items.map((each) => each.allergens)).toEqual([null, { none_of_these: true }])
  })
})

describe('what is on the menu today', () => {
  it('counts only what guests can order, under its section’s name', () => {
    const menu = {
      categories: [{ category_code: 'burgers', category_name: 'Burgers' }],
      items: [
        { name_en: 'Classic', category_code: 'burgers', base_price_cents: 549, is_active: true },
        { name_en: 'Retired', category_code: 'burgers', base_price_cents: 100, is_active: false },
      ],
    } as AdminMenu

    const today = menuToday(menu, [{ name: 'Burger combo' } as Combo])

    expect(today.items).toEqual([{ name: 'Classic', section: 'Burgers', cents: 549 }])
    expect(today.sections).toEqual(['Burgers'])
    expect(today.comboNames).toEqual(['Burger combo'])
  })

  it('is empty until the backend answers', () => {
    expect(menuToday(undefined, undefined)).toEqual(NOTHING_YET)
  })
})

describe('sections', () => {
  it('offers somewhere to start when the restaurant has none', () => {
    expect(sectionNames(draft(), NOTHING_YET)).toEqual(['Mains', 'Sides', 'Drinks', 'Desserts'])
  })

  it('keeps the menu’s sections, then the new ones', () => {
    const names = sectionNames(draft({ sections: ['Wraps'], items: [item('Taco', 'Tacos', 650)] }), MAPLE_LANE)

    expect(names).toEqual(['Burgers', 'Sides', 'Drinks', 'Wraps', 'Tacos'])
  })

  it('treats names the backend treats as one as one', () => {
    expect(sectionKey('BURGERS!')).toBe('burgers')
    expect(sectionKey('Crème brûlée')).toBe('creme_brulee')
    expect(sectionNames(draft({ sections: ['burgers'] }), MAPLE_LANE)).toEqual(['Burgers', 'Sides', 'Drinks'])
  })

  it('groups items in menu order and leaves out empty sections', () => {
    const groups = itemsBySection(draft({ items: [item('Cola', 'Drinks', 199), item('Fries', 'SIDES', 249)] }), MAPLE_LANE)

    expect(groups.map((group) => group.section)).toEqual(['Sides', 'Drinks'])
  })
})

describe('an item', () => {
  const form = { ...emptyForm('Burgers'), nameEn: 'Fish burger', price: '6.50' }

  it('goes from the form to the draft and back', () => {
    const made = draftItemFrom({ ...form, stockMode: 'counted', stock: '40', descriptionEn: 'Crispy.' }, 'a')

    expect(made).toMatchObject({ id: 'a', section: 'Burgers', name_en: 'Fish burger', base_price_cents: 650, stock: 40, description_en: 'Crispy.' })
    expect(formFromDraftItem(made)).toMatchObject({ categoryCode: 'Burgers', nameEn: 'Fish burger', price: '6.50', stockMode: 'counted', stock: '40' })
  })

  it('is unlimited unless a count is given', () => {
    expect(draftItemFrom(form, 'a').stock).toBeNull()
  })

  it('takes the cheapest size as its price and keeps the sizes through the form', () => {
    const made = draftItemFrom({ ...form, price: '', sizes: [{ key: 'a', name: 'Large', price: '9', isDefault: true }, { key: 'b', name: 'Regular', price: '6.50', isDefault: false }] }, 'a')

    expect(made.base_price_cents).toBe(650)
    expect(made.sizes).toEqual([
      { name_en: 'Large', price_cents: 900, is_default: true },
      { name_en: 'Regular', price_cents: 650, is_default: false },
    ])
    expect(priceLabel(made)).toBe('from $6.50')
    expect(formFromDraftItem(made).sizes).toMatchObject([
      { name: 'Large', price: '9.00', isDefault: true },
      { name: 'Regular', price: '6.50', isDefault: false },
    ])
  })

  it('shows a plain price when it has one', () => {
    expect(priceLabel(draftItemFrom(form, 'a'))).toBe('$6.50')
    expect(draftItemFrom(form, 'a').sizes).toBeNull()
  })

  it('starts with its allergens not reviewed', () => {
    expect(draftItemFrom(form, 'a').allergens).toBeNull()
  })

  it('keeps its reviews through an edit, and takes what was typed', () => {
    const before = item('Fish burger', 'Burgers', 650, { allergens: NONE_OF_THESE, diets: { halal: { status: 'suitable', note: '', certifiedBy: '' } } })

    const edited = draftItemFrom({ ...form, nameEn: 'Fish sandwich', price: '7' }, 'a', before)

    expect(edited).toMatchObject({ id: 'a', name_en: 'Fish sandwich', base_price_cents: 700, allergens: NONE_OF_THESE })
    expect(edited.diets.halal?.status).toBe('suitable')
  })

  it('counts toward a combo at its cheapest size', () => {
    const taco = draftItemFrom({ ...form, categoryCode: 'Tacos', price: '', sizes: [{ key: 'a', name: 'Regular', price: '6.50', isDefault: false }, { key: 'b', name: 'Large', price: '9', isDefault: false }] }, 'a')

    expect(comboParts(['Tacos'], draft({ items: [taco] }), NOTHING_YET)[0].cheapest?.cents).toBe(650)
  })

  it('needs a name, a price, and a count if it is counted', () => {
    const problems = checkItem({ ...emptyForm('Burgers'), stockMode: 'counted' }, draft(), NOTHING_YET, null)

    expect(Object.keys(problems).sort()).toEqual(['nameEn', 'price', 'stock'])
  })

  it('cannot share a name with another on the list, whatever the case', () => {
    const entered = draft({ items: [item('Fish burger', 'Burgers', 650)] })

    expect(checkItem({ ...form, nameEn: 'FISH burger ' }, entered, NOTHING_YET, null).nameEn).toMatch(/already on this list/)
    // Except itself, when it is the one being edited.
    expect(checkItem(form, entered, NOTHING_YET, 'Fish burger').nameEn).toBeUndefined()
  })

  it('cannot share a name with what stays on the menu', () => {
    const named = { ...form, nameEn: 'fries' }

    expect(checkItem(named, draft(), MAPLE_LANE, null).nameEn).toMatch(/already on your menu/)
    expect(checkItem(named, draft({ replaceMenu: true }), MAPLE_LANE, null).nameEn).toBeUndefined()
  })
})

describe('halal and kosher', () => {
  const taco = item('Taco', 'Tacos', 650)

  it('adds and clears a review for one diet only', () => {
    const both = withReview(withReview(taco, 'halal', { status: 'suitable', note: '', certifiedBy: '' }), 'kosher', { status: 'not_suitable', note: '', certifiedBy: '' })

    expect(withReview(both, 'halal', null).diets).toEqual({ kosher: { status: 'not_suitable', note: '', certifiedBy: '' } })
  })

  it('counts what has been reviewed', () => {
    const reviewed = withReview(taco, 'halal', { status: 'suitable', note: '', certifiedBy: '' })

    expect(dietCounts(draft({ items: [reviewed, item('Cola', 'Drinks', 199)] }), 'halal')).toEqual({
      certified: 0,
      suitable: 1,
      not_suitable: 0,
      unreviewed: 1,
    })
  })

  it('sets a section at once, leaving others and anything certified alone', () => {
    const certified = withReview(item('Burrito', 'Tacos', 900), 'halal', { status: 'certified', note: '', certifiedBy: 'HMA' })
    const [first, second, third] = reviewSection([taco, certified, item('Cola', 'Drinks', 199)], 'Tacos', 'halal', 'not_suitable')

    expect(first.diets.halal?.status).toBe('not_suitable')
    expect(second.diets.halal).toEqual({ status: 'certified', note: '', certifiedBy: 'HMA' })
    expect(third.diets.halal).toBeUndefined()
  })

  it('will not go live with a certificate from nobody', () => {
    const unnamed = withReview(taco, 'kosher', { status: 'certified', note: '', certifiedBy: ' ' })

    expect(stepProblem('diets', draft({ items: [unnamed] }), NOTHING_YET)).toBe('Say who certified Taco as kosher.')
  })
})

describe('a combo', () => {
  const entered = draft({ items: [item('Taco', 'Tacos', 650), item('Burrito', 'Tacos', 900), item('Slaw', 'Sides', 300)] })
  const plate = { name: 'Taco plate', savingCents: 150, slots: ['Tacos', 'Sides'] }

  it('takes the cheapest item in each part', () => {
    const parts = comboParts(plate.slots, entered, NOTHING_YET)

    expect(parts).toEqual([
      { section: 'Tacos', choices: 2, cheapest: { name: 'Taco', cents: 650 } },
      { section: 'Sides', choices: 1, cheapest: { name: 'Slaw', cents: 300 } },
    ])
    expect(cheapestSet(parts)).toBe(950)
  })

  it('counts what stays on the menu, and not what comes off', () => {
    expect(cheapestSet(comboParts(['Sides'], entered, MAPLE_LANE))).toBe(249)
    expect(cheapestSet(comboParts(['Sides'], { ...entered, replaceMenu: true }, MAPLE_LANE))).toBe(300)
  })

  it('is fine as long as it leaves something to pay', () => {
    expect(checkCombo(plate, null, entered, NOTHING_YET)).toBeNull()
    expect(checkCombo({ ...plate, savingCents: 949 }, null, entered, NOTHING_YET)).toBeNull()
  })

  it.each([
    ['no name', { name: ' ' }, 'Give the combo a name.'],
    ['one part', { slots: ['Tacos'] }, 'A combo needs at least two parts.'],
    ['a part with no items', { slots: ['Tacos', 'Wine'] }, 'Wine has no items, so this combo can’t be made.'],
    ['no saving', { savingCents: null }, 'Enter what the guest saves, like 2.00.'],
    ['a saving of nothing', { savingCents: 0 }, 'Enter what the guest saves, like 2.00.'],
    ['a saving as big as the set', { savingCents: 950 }, 'The cheapest set costs $9.50, so the saving has to be less than that.'],
  ])('refuses %s', (_, change, message) => {
    expect(checkCombo({ ...plate, ...change }, null, entered, NOTHING_YET)).toBe(message.replace('can’t', 'can\'t'))
  })

  it('cannot reuse a name, unless the old combo is going', () => {
    const named = { ...plate, name: 'burger COMBO', slots: ['Tacos', 'Sides'] }

    expect(checkCombo(named, null, entered, MAPLE_LANE)).toMatch(/already one of your combos/)
    expect(checkCombo(named, null, { ...entered, replaceMenu: true }, MAPLE_LANE)).toBeNull()
  })

  it('stops the step if an item it needed has since been removed', () => {
    const combos = [{ id: 'c', ...plate }]
    const withoutSides = { ...entered, combos, items: entered.items.filter((each) => each.section !== 'Sides') }

    expect(stepProblem('combos', withoutSides, NOTHING_YET)).toMatch(/^Taco plate: Sides has no items/)
  })
})

describe('going live', () => {
  it('needs a name and a sensible pickup time', () => {
    expect(stepProblem('restaurant', newDraft(), NOTHING_YET)).toBe('Enter the restaurant’s name.')
    const withTime = (pickupMinutes: string) => draft({ restaurant: { name: 'x', address: '', pickupMinutes } })
    expect(stepProblem('restaurant', withTime('0'), NOTHING_YET)).toMatch(/1 to 240/)
    expect(stepProblem('restaurant', withTime('soon'), NOTHING_YET)).toMatch(/1 to 240/)
    expect(stepProblem('restaurant', withTime('241'), NOTHING_YET)).toMatch(/1 to 240/)
    expect(stepProblem('restaurant', withTime('20'), NOTHING_YET)).toBeNull()
  })

  it('lists everything in the way, with where to fix it', () => {
    expect(goLiveProblems(newDraft(), NOTHING_YET).map((each) => each.step)).toEqual(['restaurant', 'menu'])
    expect(goLiveProblems(draft({ items: [item('Taco', 'Tacos', 650)] }), NOTHING_YET)).toEqual([])
  })

  it('catches a clash when switching back to adding', () => {
    const entered = draft({ items: [item('Fries', 'Sides', 299)], replaceMenu: true })

    expect(goLiveProblems(entered, MAPLE_LANE)).toEqual([])
    expect(goLiveProblems({ ...entered, replaceMenu: false }, MAPLE_LANE)[0].message).toMatch(/already on your menu/)
  })

  it('sends what the backend asks for', () => {
    const fish = item('Fish taco', 'Tacos', 650, {
      stock: 40,
      diets: {
        halal: { status: 'suitable', note: ' Own oil. ', certifiedBy: '' },
        kosher: { status: 'certified', note: 'stale', certifiedBy: ' COR ' },
      },
    })

    const sent = goLivePayload(
      draft({
        restaurant: { name: ' Harbour Grill ', address: ' ', pickupMinutes: '20' },
        replaceMenu: true,
        items: [fish],
        combos: [{ id: 'c', name: 'Plate ', savingCents: 150, slots: ['Tacos', 'Sides'] }],
      }),
    )

    expect(sent).toEqual({
      restaurant: { name: 'Harbour Grill', address: null, pickup_minutes: 20 },
      replace_menu: true,
      items: [
        {
          section: 'Tacos',
          name_en: 'Fish taco',
          name_fr: null,
          description_en: null,
          description_fr: null,
          base_price_cents: 650,
          stock: 40,
          diets: {
            halal: { status: 'suitable', note: 'Own oil.' },
            kosher: { status: 'certified', certified_by: 'COR' },
          },
          allergens: null,
          sizes: null,
        },
      ],
      combos: [{ name_en: 'Plate', discount_cents: 150, slots: ['Tacos', 'Sides'] }],
    })
  })

  it('says what went wrong in words for a manager', () => {
    expect(goLiveRefusal(new TypeError('fetch'))).toMatch(/Could not reach the backend/)
    expect(goLiveRefusal(new ApiError(409, 'duplicate_item_name', 'x'))).toMatch(/already on your menu/)
    expect(goLiveRefusal(new ApiError(422, 'combo_saving_too_large', 'x'))).toMatch(/Combos step/)
    expect(goLiveRefusal(new ApiError(500, 'unexpected_error', 'Request failed (500)'))).toBe('Request failed (500)')
  })

  it('sums it up', () => {
    expect(setupSummary({ items: 1, combos: 0 })).toBe('1 item')
    expect(setupSummary({ items: 6, combos: 1 })).toBe('6 items and 1 combo')
    expect(setupSummary({ items: 6, combos: 2 })).toBe('6 items and 2 combos')
  })
})
