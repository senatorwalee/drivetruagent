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
  /** The item's sizes, each with its real price. null: it has one price. */
  sizes: SizeRow[] | null
}

/** One size as typed. */
export interface SizeRow {
  /** Only for telling rows apart while they are edited. */
  key: string
  name: string
  price: string
  isDefault: boolean
}

/** Unlimited has no count and never runs out until someone says so. */
export type StockMode = 'unlimited' | 'counted' | 'sold_out'

/** What is wrong with the form, by field. Empty when it can be saved. */
export type FormProblems = Partial<Record<keyof ItemForm, string>>

const MAX_PRICE_CENTS = 100_000
const MAX_STOCK = 100_000
export const MAX_SIZES = 8

export function emptyForm(categoryCode: string): ItemForm {
  return { nameEn: '', nameFr: '', descriptionEn: '', descriptionFr: '', categoryCode, price: '', stockMode: 'unlimited', stock: '', sizes: null }
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
    sizes:
      item.sizes.length > 0
        ? item.sizes.map((size) => ({ key: newRowKey(), name: size.name_en, price: (size.price_cents / 100).toFixed(2), isDefault: size.is_default }))
        : null,
  }
}

function newRowKey(): string {
  return Math.random().toString(36).slice(2, 10)
}

/** The first two sizes, the first at whatever price was already typed. */
export function firstSizes(price: string): SizeRow[] {
  return [{ ...newSizeRow(), price }, newSizeRow()]
}

export function newSizeRow(): SizeRow {
  return { key: newRowKey(), name: '', price: '', isDefault: false }
}

/** The same key the backend's code_from_name gives a name, so "Large" and
 * "LARGE!" are one size here as they are there. */
export function codeFromName(name: string): string {
  return name
    .normalize('NFKD')
    .replace(/[^\p{ASCII}]/gu, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

/** What is wrong with the sizes as typed, or undefined. */
function sizesProblem(sizes: SizeRow[]): string | undefined {
  if (sizes.some((size) => !size.name.trim())) return 'Give each size a name.'
  const codes = sizes.map((size) => codeFromName(size.name))
  if (new Set(codes).size !== codes.length) return 'Two sizes have the same name.'
  const prices = sizes.map((size) => parsePrice(size.price))
  if (prices.some((cents) => cents === null)) return 'Enter a price for each size, like 5.49.'
  if (prices.some((cents) => cents !== null && cents > MAX_PRICE_CENTS)) return 'That is more than $1,000. Check the price.'
  return undefined
}

/** What to send for sizes. Call only when checkForm finds nothing. */
function sizesFrom(sizes: SizeRow[]): NonNullable<NewItem['sizes']> {
  return sizes.map((size) => ({ name_en: size.name.trim(), price_cents: parsePrice(size.price) ?? 0, is_default: size.isDefault }))
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
  if (form.sizes) {
    problems.sizes = sizesProblem(form.sizes)
    if (!problems.sizes) delete problems.sizes
  } else {
    const cents = parsePrice(form.price)
    if (cents === null) problems.price = 'Enter a price, like 5.49.'
    else if (cents > MAX_PRICE_CENTS) problems.price = 'That is more than $1,000. Check the price.'
  }
  const stock = stockFrom(form)
  if (stock === undefined) problems.stock = 'Enter how many you have, as a whole number.'
  else if (stock !== null && stock > MAX_STOCK) problems.stock = 'That is more than 100,000. Check the count.'
  if (form.descriptionFr.trim() && !form.descriptionEn.trim()) {
    problems.descriptionEn = 'Add an English description; it is used when French is missing.'
  }
  return problems
}

/** The request for a new item. Call only when checkForm finds nothing. */
export function newItemFrom(form: ItemForm): NewItem {
  const sizes = form.sizes && sizesFrom(form.sizes)
  return {
    name_en: form.nameEn.trim(),
    name_fr: form.nameFr.trim() || null,
    description_en: form.descriptionEn.trim() || null,
    description_fr: form.descriptionFr.trim() || null,
    category_code: form.categoryCode,
    // The cheapest size is the item's price; the others add to it.
    base_price_cents: sizes ? Math.min(...sizes.map((size) => size.price_cents)) : (parsePrice(form.price) ?? 0),
    stock: stockFrom(form) ?? null,
    sizes,
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
  if (JSON.stringify(wanted.sizes ?? []) !== JSON.stringify(item.sizes)) changes.sizes = wanted.sizes
  return changes
}

export function sameForm(a: ItemForm, b: ItemForm): boolean {
  const sameText = (Object.keys(a) as (keyof ItemForm)[]).every((key) => key === 'sizes' || a[key].trim() === b[key].trim())
  return sameText && sameSizes(a.sizes, b.sizes)
}

function sameSizes(a: SizeRow[] | null, b: SizeRow[] | null): boolean {
  const typed = (rows: SizeRow[] | null) => rows?.map(({ name, price, isDefault }) => [name.trim(), price.trim(), isDefault])
  return JSON.stringify(typed(a)) === JSON.stringify(typed(b))
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
      return { field: form.sizes ? 'sizes' : 'price', message: 'A special takes at least this much off the item. Set a higher price.' }
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
