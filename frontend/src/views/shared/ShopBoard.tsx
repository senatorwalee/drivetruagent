import type { WeekOfSpecials } from '../../api/client'
import { money } from '../../session/presentation'
import { isLow, type StockGroup } from '../../session/board'
import styles from './ShopBoard.module.css'

interface ShopBoardProps {
  week: WeekOfSpecials | null
  stock: StockGroup[]
  /** Placement within the host view's layout. */
  className?: string
}

/**
 * The restaurant's side of things, for anyone watching a demo: the special
 * for every day of this week with today marked, and stock counting down as
 * carts fill and orders are placed. Styled from the host view's own colours,
 * so it belongs in all three.
 */
export function ShopBoard({ week, stock, className }: ShopBoardProps) {
  return (
    <section aria-label="Specials and stock" className={`${styles.board} ${className ?? ''}`}>
      <div className={styles.part}>
        <h2 className={styles.title}>This week's specials</h2>
        {week ? (
          <ol className={styles.week}>
            {week.days.map((day) => (
              <li key={day.date} className={styles.day} aria-current={day.is_today ? 'date' : undefined}>
                <span className={styles.dayName}>
                  {day.day_name}
                  {day.is_today && <span className={styles.today}>today</span>}
                </span>
                {day.specials.length === 0 && <span className={styles.none}>No special</span>}
                {day.specials.map((special) => (
                  <span key={special.code} className={styles.special} data-sold-out={!special.in_stock}>
                    <span className={styles.specialItem}>{special.item_name}</span>
                    <span className={styles.prices}>
                      <span className={styles.price}>{money(special.special_price_cents)}</span>
                      <s className={styles.was}>{money(special.regular_price_cents)}</s>
                    </span>
                    {!special.in_stock && <span className={styles.soldOut}>sold out</span>}
                  </span>
                ))}
              </li>
            ))}
          </ol>
        ) : (
          <p className={styles.none}>Loading…</p>
        )}
      </div>

      <div className={styles.part}>
        <h2 className={styles.title}>
          Stock <span className={styles.titleNote}>available now · held in carts</span>
        </h2>
        <div className={styles.groups}>
          {stock.map((group) => (
            <div key={group.categoryName} className={styles.group}>
              <h3 className={styles.groupName}>{group.categoryName}</h3>
              <ul className={styles.items}>
                {group.rows.map((row) => (
                  <li key={row.itemCode} className={styles.item} data-low={isLow(row)} data-special={row.special}>
                    <span className={styles.itemName}>{row.itemName}</span>
                    {/* Keyed by the number, so it flashes each time it changes. */}
                    <span key={row.available} className={styles.count} aria-label={`${row.available} available`}>
                      {row.available}
                    </span>
                    <span className={styles.held}>{row.held > 0 ? `${row.held} held` : ''}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
