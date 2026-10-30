/**
 * A made-up restaurant for showing the page without setting one up. Every step
 * has something on it, and one item is left not reviewed on purpose, to show
 * what that looks like.
 */
import { NONE_OF_THESE } from '../menu-admin/allergens'
import { newDraft } from './words'
import type { Draft, DraftItem } from './words'

function item(id: string, section: string, name: string, cents: number, details: Partial<DraftItem> = {}): DraftItem {
  return { id, section, name_en: name, name_fr: null, description_en: null, description_fr: null, base_price_cents: cents, stock: null, diets: {}, allergens: null, sizes: null, ...details }
}

export function sampleDraft(): Draft {
  return {
    ...newDraft(),
    restaurant: { name: 'Harbour Grill', address: '12 Water Street, Kingston', pickupMinutes: '20' },
    items: [
      item('fish-taco', 'Mains', 'Fish taco', 650, {
        allergens: { noneOfThese: false, contains: ['fish', 'wheat', 'gluten'], mayContain: [{ allergen: 'sesame', because: 'Toasted beside the sesame buns.' }] },
        diets: { halal: { status: 'suitable', note: 'Fish only, fried in its own oil.', certifiedBy: '' } },
      }),
      item('chicken-wrap', 'Mains', 'Grilled chicken wrap', 750, {
        sizes: [
          { name_en: 'Regular', price_cents: 750, is_default: true },
          { name_en: 'Large', price_cents: 950, is_default: false },
        ],
        allergens: { noneOfThese: false, contains: ['wheat', 'gluten', 'mustard'], mayContain: [] },
        diets: { halal: { status: 'certified', note: '', certifiedBy: 'Sample Certifier' } },
      }),
      item('fries', 'Sides', 'Fries', 300, {
        sizes: [
          { name_en: 'Small', price_cents: 300, is_default: false },
          { name_en: 'Medium', price_cents: 375, is_default: false },
          { name_en: 'Large', price_cents: 425, is_default: false },
        ],
        allergens: NONE_OF_THESE,
        diets: { halal: { status: 'suitable', note: 'Fried in their own oil.', certifiedBy: '' } },
      }),
      item('slaw-cup', 'Sides', 'Slaw cup', 300, { stock: 40, allergens: { noneOfThese: false, contains: ['egg', 'mustard'], mayContain: [] } }),
      item('lemonade', 'Drinks', 'Lemonade', 275, { allergens: NONE_OF_THESE }),
      // Not reviewed: the agent hands questions about it to a person.
      item('hand-pie', 'Desserts', 'Apple hand pie', 350),
    ],
    combos: [{ id: 'taco-plate', name: 'Taco plate', savingCents: 150, slots: ['Mains', 'Sides'] }],
  }
}
