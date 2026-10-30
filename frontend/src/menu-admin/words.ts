/**
 * The menu page's plain logic: reading what was typed into the form, and the
 * words shown for an item. No React here, so it is tested on its own.
 */
import { ApiError } from '../api/client'
import type { AdminItem, ItemChanges, NewItem } from '../api/client'
import { money } from '../session/presentation'

/** What the form holds, as typed. Numbers stay text until they are saved. */
export interface ItemForm {
  nameEn: string
  nameFr: string
  descriptionEn: string
  descriptionFr: string
  categoryCode: string
  price: string
  stockMode: StockMode
  /** How many, when the mode is counted. */
  stock: string
}

/** Unlimited has no count and never runs out until someone says so. */
export type StockMode = 'unlimited' | 'counted' | 'sold_out'

/** What is wrong with the form, by field. Empty when it can be saved. */
export type FormProblems = Partial<Record<keyof ItemForm, string>>

const MAX_PRICE_CENTS = 100_000

export function emptyForm(categoryCode: string): ItemForm {
  return { nameEn: '', nameFr: '', descriptionEn: '', descriptionFr: '', categoryCode, price: '', stockMode: 'unlimited', stock: '' }
}

export function formFromItem(item: AdminItem): ItemForm {
  return {
    nameEn: item.name_en,
    nameFr: item.name_fr ?? '',
    descriptionEn: item.description_en ?? '',
    descriptionFr: item.description_fr ?? '',
    categoryCode: item.category_code,
    price: (item.base_price_cents / 100).toFixed(2),
    stockMode: item.stock === null ? 'unlimited' : item.stock === 0 ? 'sold_out' : 'counted',
    stock: item.stock ? String(item.stock) : '',
  }
}

/** "5", "5.4", "$5.49" → cents. null if it is not a price. */
export function parsePrice(text: string): number | null {
  const match = /^\$?\s*(\d{1,4})(?:\.(\d{1,2}))?$/.exec(text.trim())
  if (!match) return null
  return Number(match[1]) * 100 + Number((match[2] ?? '0').padEnd(2, '0'))
}

/** Blank → not tracked (null). A whole number → units. Anything else → undefined. */
export function parseStock(text: string): number | null | undefined {
  const trimmed = text.trim()
  if (trimmed === '') return null
  return /^\d{1,6}$/.test(trimmed) ? Number(trimmed) : undefined
}

/** What to send for stock: null for unlimited. undefined if the count isn't one. */
export function stockFrom(form: Pick<ItemForm, 'stockMode' | 'stock'>): number | null | undefined {
  if (form.stockMode === 'unlimited') return null
  if (form.stockMode === 'sold_out') return 0
  return form.stock.trim() === '' ? undefined : parseStock(form.stock)
}

export function checkForm(form: ItemForm): FormProblems {
  const problems: FormProblems = {}
  if (!form.nameEn.trim()) problems.nameEn = 'Give the item a name.'
  const cents = parsePrice(form.price)
  if (cents === null) problems.price = 'Enter a price, like 5.49.'
  else if (cents > MAX_PRICE_CENTS) problems.price = 'That is more than $1,000. Check the price.'
  if (stockFrom(form) === undefined) problems.stock = 'Enter how many you have, as a whole number.'
  if (form.descriptionFr.trim() && !form.descriptionEn.trim()) {
    problems.descriptionEn = 'Add an English description; it is used when French is missing.'
  }
  return problems
}

/** The request for a new item. Call only when checkForm finds nothing. */
export function newItemFrom(form: ItemForm): NewItem {
  return {
    name_en: form.nameEn.trim(),
    name_fr: form.nameFr.trim() || null,
    description_en: form.descriptionEn.trim() || null,
    description_fr: form.descriptionFr.trim() || null,
    category_code: form.categoryCode,
    base_price_cents: parsePrice(form.price) ?? 0,
    stock: stockFrom(form) ?? null,
  }
}

/** Only what differs from the item as saved. Empty when nothing changed. */
export function changesFrom(form: ItemForm, item: AdminItem): ItemChanges {
  const wanted = newItemFrom(form)
  const changes: ItemChanges = {}
  if (wanted.name_en !== item.name_en) changes.name_en = wanted.name_en
  if (wanted.name_fr !== item.name_fr) changes.name_fr = wanted.name_fr
  if (wanted.description_en !== item.description_en) changes.description_en = wanted.description_en
  if (wanted.description_fr !== item.description_fr) changes.description_fr = wanted.description_fr
  if (wanted.category_code !== item.category_code) changes.category_code = wanted.category_code
  if (wanted.base_price_cents !== item.base_price_cents) changes.base_price_cents = wanted.base_price_cents
  if (wanted.stock !== item.stock) changes.stock = wanted.stock
  return changes
}

export function sameForm(a: ItemForm, b: ItemForm): boolean {
  return (Object.keys(a) as (keyof ItemForm)[]).every((key) => a[key].trim() === b[key].trim())
}

export function stockLabel(item: AdminItem): string {
  if (item.stock === null) return 'Unlimited'
  if (item.sold_out) return 'Sold out'
  return `${item.stock} in stock`
}

/** "Small, Medium +$0.50, Large +$1.30" */
export function optionsLabel(group: AdminItem['choices'][number]): string {
  return group.options
    .map((option) => (option.price_delta_cents ? `${option.option_name} +${money(option.price_delta_cents)}` : option.option_name))
    .join(', ')
}

/** What the backend said, in words for a manager, and which field it is about. */
export function explainError(error: unknown, form: ItemForm): { field?: keyof ItemForm | 'photo'; message: string } {
  if (!(error instanceof ApiError)) {
    return { message: 'Could not reach the backend. Check it is running, then try again.' }
  }
  switch (error.code) {
    case 'duplicate_item_name':
      return { field: 'nameEn', message: `Another item on the menu is already called “${form.nameEn.trim()}”.` }
    case 'price_below_special':
      return { field: 'price', message: 'A special takes at least this much off the item. Set a higher price.' }
    case 'unknown_category':
      return { field: 'categoryCode', message: 'That category no longer exists. Reload the page.' }
    case 'photo_not_accepted':
      return { field: 'photo', message: photoProblem(error.message) }
    default:
      return { message: error.message }
  }
}

function photoProblem(message: string): string {
  return `The photo was not saved: ${message}`
}
