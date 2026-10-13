import { money, plainAmount } from '../../session/presentation'
import type { LiveSession } from '../../session/useLiveSession'
import { DiscountRows } from '../shared/DiscountRows'
import styles from './ControlRoom.module.css'

/** The cart, compact: line number, item, options and amount. */
export function CartTable({ live }: { live: LiveSession }) {
  const { cart, timeline } = live
  const lines = cart?.lines ?? []

  return (
    <aside aria-label="Order" className={styles.column}>
      <div className={styles.panelHead}>
        <h2 className={styles.panelTitle}>Cart</h2>
        {timeline.order && <span className={styles.panelNote}>order {timeline.order.orderNumber} placed</span>}
      </div>

      {lines.length === 0 && !timeline.pending && <p className={styles.hint}>Empty.</p>}

      <ul className={styles.cartLines}>
        {lines.map((line) => (
          <li key={line.line_no} className={styles.cartLine}>
            <span className={styles.cartLineNumber}>L{line.line_no}</span>
            <span className={styles.cartLineName}>
              {line.quantity} × {line.item_name}
            </span>
            <span className={styles.amount}>{(line.line_total_cents / 100).toFixed(2)}</span>
            {line.options.length > 0 && (
              <span className={styles.cartLineOptions}>{line.options.map((option) => option.option_name).join(' · ')}</span>
            )}
          </li>
        ))}
      </ul>

      {timeline.pending && (
        <div className={styles.pending}>
          <span className={styles.pendingLabel}>Pending · not in the cart</span>
          <span className={styles.cartLineName}>
            {timeline.pending.quantity} × {live.itemName(timeline.pending.itemCode)}
          </span>
        </div>
      )}

      <dl className={styles.totals}>
        <dt>subtotal</dt>
        <dd>{((cart?.subtotal_cents ?? 0) / 100).toFixed(2)}</dd>
        <DiscountRows discounts={cart?.discounts} className={styles.saving} format={plainAmount} />
        <dt>tax</dt>
        <dd>{((cart?.tax_cents ?? 0) / 100).toFixed(2)}</dd>
        <dt className={styles.totalLabel}>Total</dt>
        <dd className={styles.totalValue}>{money(cart?.total_cents ?? 0)}</dd>
      </dl>
    </aside>
  )
}
