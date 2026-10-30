/**
 * The onboarding page's plain logic: the draft, what is wrong with it, what a
 * combo works out to, and what is sent at the end. No React here, so it is
 * tested on its own.
 */
import { ApiError } from '../api/client'
import type { AdminMenu, Combo, Diet, DietStatus, Onboarding, OnboardingResult } from '../api/client'
import { checkForm, formFromItem, newItemFrom, parsePrice, stockFrom } from '../menu-admin/words'
import type { FormProblems, ItemForm } from '../menu-admin/words'
import { money } from '../session/presentation'

export const STEPS = [
  { id: 'restaurant', label: 'Restaurant' },
  { id: 'menu', label: 'Menu' },
  { id: 'diets', label: 'Halal and kosher' },
  { id: 'combos', label: 'Combos' },
  { id: 'review', label: 'Go live' },
] as const

export type Step = (typeof STEPS)[number]['id']

export const DIETS: { id: Diet; label: string }[] = [
  { id: 'halal', label: 'Halal' },
  { id: 'kosher', label: 'Kosher' },
]

/** For a restaurant with no sections yet. */
const STARTING_SECTIONS = ['Mains', 'Sides', 'Drinks', 'Desserts']
const MAX_PICKUP_MINUTES = 240

export interface DraftReview {
  status: DietStatus
  /** Why, in words a guest can hear. Optional. */
  note: string
  /** Who issued the certificate, when certified. */
  certifiedBy: string
}

export interface DraftItem {
  id: string
  section: string
  name_en: string
  name_fr: string | null
  description_en: string | null
  description_fr: string | null
  base_price_cents: number
  /** Units on hand; null for unlimited. */
  stock: number | null
  diets: Partial<Record<Diet, DraftReview>>
}

export interface DraftCombo {
  id: string
  name: string
  savingCents: number
  /** Section names. The first is the main item. */
  slots: string[]
}

/** Everything entered so far. Nothing reaches the backend until Go live. */
export interface Draft {
  version: 1
  step: Step
  restaurant: { name: string; address: string; pickupMinutes: string }
  /** Sections added by hand. Others come from the menu and the items. */
  sections: string[]
  items: DraftItem[]
  combos: DraftCombo[]
  /** Start fresh: what is on the menu now comes off at Go live. */
  replaceMenu: boolean
}

export function newDraft(): Draft {
  return {
    version: 1,
    step: 'restaurant',
    restaurant: { name: '', address: '', pickupMinutes: '15' },
    sections: [],
    items: [],
    combos: [],
    replaceMenu: false,
  }
}

// --- Keeping it -------------------------------------------------------------

const DRAFT_KEY = 'frontdesk.onboarding.draft'

/** The draft from this device, if there is one. Storage can be turned off. */
export function loadDraft(): Draft {
  try {
    const stored = JSON.parse(window.localStorage.getItem(DRAFT_KEY) ?? 'null') as Draft | null
    if (stored?.version === 1) return stored
  } catch {
    // Unreadable, or storage is blocked: start clean.
  }
  return newDraft()
}

export function saveDraft(draft: Draft): void {
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
  } catch {
    // Storage is full or blocked; the draft still lives in the page.
  }
}

export function newId(): string {
  return Math.random().toString(36).slice(2, 10)
}

// --- What is on the menu now ------------------------------------------------

export interface MenuToday {
  items: { name: string; section: string; cents: number }[]
  sections: string[]
  comboNames: string[]
}

export function menuToday(menu: AdminMenu | undefined, combos: Combo[] | undefined): MenuToday {
  const sectionName = new Map(menu?.categories.map((each) => [each.category_code, each.category_name]))
  return {
    items: (menu?.items ?? [])
      .filter((item) => item.is_active)
      .map((item) => ({ name: item.name_en, section: sectionName.get(item.category_code) ?? item.category_code, cents: item.base_price_cents })),
    sections: menu?.categories.map((each) => each.category_name) ?? [],
    comboNames: combos?.map((combo) => combo.name) ?? [],
  }
}

// --- Sections ---------------------------------------------------------------

/** The same key the backend's code_from_name gives a name, so "BURGERS" and
 * "Burgers" are one section here as they are there. */
export function sectionKey(name: string): string {
  return name
    .normalize('NFKD')
    .replace(/[^\p{ASCII}]/gu, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

function sameName(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase()
}

/** Every section to choose from, in menu order, each once. */
export function sectionNames(draft: Draft, today: MenuToday): string[] {
  const starting = today.sections.length > 0 ? today.sections : STARTING_SECTIONS
  const names = new Map<string, string>()
  for (const name of [...starting, ...draft.sections, ...draft.items.map((item) => item.section)]) {
    if (!names.has(sectionKey(name))) names.set(sectionKey(name), name)
  }
  return [...names.values()]
}

export function itemsBySection(draft: Draft, today: MenuToday): { section: string; items: DraftItem[] }[] {
  return sectionNames(draft, today)
    .map((section) => ({ section, items: draft.items.filter((item) => sectionKey(item.section) === sectionKey(section)) }))
    .filter((group) => group.items.length > 0)
}

// --- Items ------------------------------------------------------------------

/** A form's words for a draft item. Keeps what the item already has. */
export function draftItemFrom(form: ItemForm, id: string, diets: DraftItem['diets']): DraftItem {
  const { category_code: section, ...fields } = newItemFrom(form)
  return { ...fields, id, section, name_fr: fields.name_fr ?? null, description_en: fields.description_en ?? null, description_fr: fields.description_fr ?? null, stock: stockFrom(form) ?? null, diets }
}

export function formFromDraftItem(item: DraftItem): ItemForm {
  return formFromItem({
    item_code: item.id,
    category_code: item.section,
    name_en: item.name_en,
    name_fr: item.name_fr,
    description_en: item.description_en,
    description_fr: item.description_fr,
    base_price_cents: item.base_price_cents,
    is_active: true,
    stock: item.stock,
    sold_out: false,
    photo: null,
    allergens_reviewed_on: null,
    choices: [],
  })
}

export function checkItem(form: ItemForm, draft: Draft, today: MenuToday, editing: string | null): FormProblems {
  const problems = checkForm(form)
  const name = form.nameEn.trim()
  if (!problems.nameEn && draft.items.some((item) => item.id !== editing && sameName(item.name_en, name))) {
    problems.nameEn = `“${name}” is already on this list.`
  }
  if (!problems.nameEn && !draft.replaceMenu && today.items.some((item) => sameName(item.name, name))) {
    problems.nameEn = `“${name}” is already on your menu. Use another name, or choose Start fresh.`
  }
  return problems
}

export function quantityLabel(item: DraftItem): string {
  return item.stock === null ? 'Unlimited' : `${item.stock} on hand`
}

// --- Halal and kosher -------------------------------------------------------

export function dietCounts(draft: Draft, diet: Diet): Record<DietStatus | 'unreviewed', number> {
  const counts = { certified: 0, suitable: 0, not_suitable: 0, unreviewed: 0 }
  for (const item of draft.items) counts[item.diets[diet]?.status ?? 'unreviewed'] += 1
  return counts
}

/** Set a whole section at once. Certified items keep their paperwork. */
export function reviewSection(items: DraftItem[], section: string, diet: Diet, status: DietStatus | null): DraftItem[] {
  return items.map((item) => {
    if (sectionKey(item.section) !== sectionKey(section) || item.diets[diet]?.status === 'certified') return item
    return withReview(item, diet, status === null ? null : { status, note: item.diets[diet]?.note ?? '', certifiedBy: '' })
  })
}

export function withReview(item: DraftItem, diet: Diet, review: DraftReview | null): DraftItem {
  const { [diet]: _old, ...others } = item.diets
  return { ...item, diets: review ? { ...others, [diet]: review } : others }
}

function dietsSent(item: DraftItem): Onboarding['items'][number]['diets'] {
  const sent: Onboarding['items'][number]['diets'] = {}
  for (const { id } of DIETS) {
    const review = item.diets[id]
    if (!review) continue
    sent[id] =
      review.status === 'certified'
        ? { status: review.status, certified_by: review.certifiedBy.trim() }
        : { status: review.status, note: review.note.trim() || null }
  }
  return sent
}

// --- Combos -----------------------------------------------------------------

export interface ComboPart {
  section: string
  /** How many items count for this part. */
  choices: number
  cheapest: { name: string; cents: number } | null
}

/** Each part of a combo, from the items that will be on the menu at Go live. */
export function comboParts(slots: string[], draft: Draft, today: MenuToday): ComboPart[] {
  const onTheMenu = [
    ...draft.items.map((item) => ({ name: item.name_en, section: item.section, cents: item.base_price_cents })),
    ...(draft.replaceMenu ? [] : today.items),
  ]
  return slots.map((section) => {
    const fits = onTheMenu.filter((item) => sectionKey(item.section) === sectionKey(section)).sort((a, b) => a.cents - b.cents)
    return { section, choices: fits.length, cheapest: fits[0] ? { name: fits[0].name, cents: fits[0].cents } : null }
  })
}

export function cheapestSet(parts: ComboPart[]): number {
  return parts.reduce((sum, part) => sum + (part.cheapest?.cents ?? 0), 0)
}

/** What is wrong with a combo as entered, or null. The backend checks the same. */
export function checkCombo(
  combo: { name: string; savingCents: number | null; slots: string[] },
  id: string | null,
  draft: Draft,
  today: MenuToday,
): string | null {
  const name = combo.name.trim()
  if (!name) return 'Give the combo a name.'
  if (draft.combos.some((each) => each.id !== id && sameName(each.name, name))) return `“${name}” is already on this list.`
  if (!draft.replaceMenu && today.comboNames.some((each) => sameName(each, name))) return `“${name}” is already one of your combos.`
  if (combo.slots.length < 2) return 'A combo needs at least two parts.'
  const parts = comboParts(combo.slots, draft, today)
  const empty = parts.find((part) => part.choices === 0)
  if (empty) return `${empty.section} has no items, so this combo can't be made.`
  if (combo.savingCents === null || combo.savingCents <= 0) return 'Enter what the guest saves, like 2.00.'
  const total = cheapestSet(parts)
  if (combo.savingCents >= total) return `The cheapest set costs ${money(total)}, so the saving has to be less than that.`
  return null
}

export function parseSaving(text: string): number | null {
  return parsePrice(text)
}

// --- Is it ready ------------------------------------------------------------

export function restaurantProblem(draft: Draft): string | null {
  if (!draft.restaurant.name.trim()) return 'Enter the restaurant’s name.'
  const minutes = draft.restaurant.pickupMinutes.trim()
  if (!/^\d{1,3}$/.test(minutes) || Number(minutes) < 1 || Number(minutes) > MAX_PICKUP_MINUTES) {
    return `Enter a pickup time from 1 to ${MAX_PICKUP_MINUTES} minutes.`
  }
  return null
}

function menuProblem(draft: Draft): string | null {
  return draft.items.length === 0 ? 'Add at least one item.' : null
}

function dietProblem(draft: Draft): string | null {
  for (const item of draft.items) {
    for (const { id, label } of DIETS) {
      const review = item.diets[id]
      if (review?.status === 'certified' && !review.certifiedBy.trim()) return `Say who certified ${item.name_en} as ${label.toLowerCase()}.`
    }
  }
  return null
}

function clashProblem(draft: Draft, today: MenuToday): string | null {
  if (draft.replaceMenu) return null
  const clash = draft.items.find((item) => today.items.some((each) => sameName(each.name, item.name_en)))
  return clash ? `“${clash.name_en}” is already on your menu. Rename it, or choose Start fresh on the Menu step.` : null
}

/** What stops this step from being finished, or null. */
export function stepProblem(step: Step, draft: Draft, today: MenuToday): string | null {
  switch (step) {
    case 'restaurant':
      return restaurantProblem(draft)
    case 'menu':
      return menuProblem(draft) ?? clashProblem(draft, today)
    case 'diets':
      return dietProblem(draft)
    case 'combos':
      for (const combo of draft.combos) {
        const problem = checkCombo(combo, combo.id, draft, today)
        if (problem) return `${combo.name}: ${problem}`
      }
      return null
    case 'review':
      return null
  }
}

/** Everything still in the way of going live, each with where to fix it. */
export function goLiveProblems(draft: Draft, today: MenuToday): { step: Step; label: string; message: string }[] {
  return STEPS.flatMap(({ id, label }) => {
    const message = stepProblem(id, draft, today)
    return message ? [{ step: id, label, message }] : []
  })
}

// --- Going live -------------------------------------------------------------

export function goLivePayload(draft: Draft): Onboarding {
  return {
    restaurant: {
      name: draft.restaurant.name.trim(),
      address: draft.restaurant.address.trim() || null,
      pickup_minutes: Number(draft.restaurant.pickupMinutes.trim()),
    },
    replace_menu: draft.replaceMenu,
    items: draft.items.map((item) => ({
      section: item.section,
      name_en: item.name_en,
      name_fr: item.name_fr,
      description_en: item.description_en,
      description_fr: item.description_fr,
      base_price_cents: item.base_price_cents,
      stock: item.stock,
      diets: dietsSent(item),
    })),
    combos: draft.combos.map((combo) => ({ name_en: combo.name.trim(), discount_cents: combo.savingCents, slots: combo.slots })),
  }
}

export function goLiveRefusal(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Could not reach the backend. Check it is running, then try again.'
  switch (error.code) {
    case 'duplicate_item_name':
      return 'One of these items is already on your menu. Rename it, or choose Start fresh on the Menu step.'
    case 'duplicate_combo_name':
      return 'One of these combos has the same name as one you already have.'
    case 'combo_needs_items':
    case 'combo_saving_too_large':
      return 'One of the combos no longer adds up. Check the Combos step.'
    default:
      return error.message
  }
}

/** "6 items and 1 combo" */
export function setupSummary(result: Pick<OnboardingResult, 'items' | 'combos'>): string {
  const items = `${result.items} ${result.items === 1 ? 'item' : 'items'}`
  if (result.combos === 0) return items
  return `${items} and ${result.combos} ${result.combos === 1 ? 'combo' : 'combos'}`
}
