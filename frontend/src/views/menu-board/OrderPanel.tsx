import { itemsLabel, money } from '../../session/presentation'
import type { LiveSession } from '../../session/useLiveSession'
import { DiscountRows } from '../shared/DiscountRows'
import { AlertIcon, CheckIcon } from '../shared/icons'
import styles from './MenuBoard.module.css'

/** The order as the customer would see it on the confirmation screen. */
export function OrderPanel({ live }: { live: LiveSession }) {
  const { cart, timeline } = live
  const lines = cart?.lines ?? []

  return (
    <aside aria-label="Order" className={styles.order}>
      <div className={styles.orderHeading}>
        <h2 className={styles.orderTitle}>Your order</h2>
        <span className={styles.orderCount}>{itemsLabel(cart?.item_count ?? 0)}</span>
      </div>

      {lines.length === 0 && !timeline.pending && <p className={styles.orderEmpty}>Nothing yet. Items appear here as they are added.</p>}

      <ul className={styles.lines}>
        {lines.map((line) => (
          <li key={line.line_no} className={styles.line}>
            <span className={styles.lineNumber}>L{line.line_no}</span>
            <span className={styles.lineBody}>
              <span className={styles.lineName}>
                {line.quantity} × {line.item_name}
              </span>
              {line.options.length > 0 && (
                <span className={styles.lineOptions}>{line.options.map((option) => option.option_name).join(' · ')}</span>
              )}
            </span>
            <span className={styles.price}>{money(line.line_total_cents)}</span>
          </li>
        ))}
      </ul>

      {timeline.pending && (
        <div className={styles.pending}>
          <AlertIcon size={22} />
          <div className={styles.lineBody}>
            <span className={styles.pendingLabel}>Waiting for a yes</span>
            <span className={styles.lineName}>
              {timeline.pending.quantity} × {live.itemName(timeline.pending.itemCode)}
            </span>
            <span className={styles.lineOptions}>Not added yet. The agent is checking the quantity.</span>
          </div>
        </div>
      )}

      {timeline.order && (
        <div className={styles.placed}>
          <CheckIcon size={20} />
          <span>Order {timeline.order.orderNumber} placed. Please pull forward.</span>
        </div>
      )}

      <dl className={styles.totals}>
        <dt>Subtotal</dt>
        <dd>{money(cart?.subtotal_cents ?? 0)}</dd>
        <DiscountRows discounts={cart?.discounts} className={styles.saving} />
        <dt>Tax</dt>
        <dd>{money(cart?.tax_cents ?? 0)}</dd>
        <dt className={styles.totalLabel}>Total</dt>
        <dd className={styles.totalValue}>{money(cart?.total_cents ?? 0)}</dd>
      </dl>
    </aside>
  )
}
