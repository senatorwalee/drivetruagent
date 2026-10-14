/**
 * Turns stock levels and the week's specials into what the board on the live
 * screen shows. Pure functions: no React and no network.
 */
import type { Menu, StockLevel, WeekOfSpecials } from '../api/client'

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
