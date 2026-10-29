/**
 * Turns stock levels, the week's specials and the combos into what the board
 * on the live screen shows. Pure functions: no React and no network.
 */
import type { Cart, Combo, Menu, StockLevel, WeekOfSpecials } from '../api/client'

export interface StockRow {
  itemCode: string
  itemName: string
  /** Units physically there. Goes down when an order is placed. */
  onHand: number
  /** Units sitting in carts that have not been ordered yet. */
  held: number
  /** What another customer could still have. Goes down as carts fill. */
  available: number
  /** The item is today's special. */
  special: boolean
}

export interface StockGroup {
  categoryName: string
  rows: StockRow[]
}

/**
 * Stock by menu category, in menu order, with today's special marked. Items
 * whose stock is not tracked are left out: there is no number to show.
 */
export function stockGroups(menu: Menu | undefined, levels: readonly StockLevel[], week: WeekOfSpecials | null): StockGroup[] {
  if (!menu) return []
  const byCode = new Map(levels.map((level) => [level.item_code, level]))
  const special = new Set(todaysSpecials(week).map((today) => today.item_code))
  return menu.categories
    .map((category) => ({
      categoryName: category.category_name,
      rows: category.items.flatMap((item) => {
        const level = byCode.get(item.item_code)
        if (!level) return []
        return [
          {
            itemCode: item.item_code,
            itemName: item.item_name,
            onHand: level.on_hand,
            held: level.reserved,
            available: level.available,
            special: special.has(item.item_code),
          },
        ]
      }),
    }))
    .filter((group) => group.rows.length > 0)
}

/** The specials running today, from the week. */
export function todaysSpecials(week: WeekOfSpecials | null) {
  return week?.days.find((day) => day.is_today)?.specials ?? []
}

/** Few enough left that someone should notice. */
export function isLow(row: Pick<StockRow, 'available'>): boolean {
  return row.available <= 5
}

export interface ComboSlotState {
  categoryName: string
  /** An item in the cart that fills the slot; null while it is empty. */
  itemName: string | null
}

export interface ComboState {
  code: string
  name: string
  savesCents: number
  /** How many times the cart has earned it, as the backend priced it. */
  times: number
  slots: ComboSlotState[]
}

/**
 * Each combo with what the cart has toward it: an item from the cart in each
 * slot it fills, in the order they were added. Whether the combo applies is
 * the backend's word (the cart's discounts), never worked out here; filling
 * the slots only shows why.
 */
export function comboStates(combos: readonly Combo[], menu: Menu | undefined, cart: Cart | null): ComboState[] {
  const categoryOf = new Map(menu?.categories.flatMap((category) => category.items.map((item) => [item.item_code, category.category_code])) ?? [])
  return combos.map((combo) => {
    // Every unit in the cart by category, so a combo that needs two of
    // something takes two.
    const units = new Map<string, string[]>()
    for (const line of cart?.lines ?? []) {
      const category = categoryOf.get(line.item_code)
      if (!category) continue
      const queue = units.get(category) ?? []
      for (let unit = 0; unit < line.quantity; unit += 1) queue.push(line.item_name)
      units.set(category, queue)
    }
    const applied = cart?.discounts.find((discount) => discount.kind === 'combo' && discount.code === combo.code)
    return {
      code: combo.code,
      name: combo.name,
      savesCents: combo.saves_cents,
      times: applied?.times ?? 0,
      slots: combo.slots.map((slot) => ({
        categoryName: slot.category_name,
        itemName: units.get(slot.category_code)?.shift() ?? null,
      })),
    }
  })
}
