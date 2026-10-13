import { money } from '../../session/presentation'
import type { LiveSession } from '../../session/useLiveSession'
import styles from './Receipt.module.css'

/** The order printed as a till receipt, with a stamp for anything on hold. */
export function ReceiptTape({ live }: { live: LiveSession }) {
  const { cart, timeline } = live
  const lines = cart?.lines ?? []

  return (
    <aside aria-label="Order" className={styles.tapeColumn}>
      <div className={styles.tape}>
        <div className={styles.tapeHead}>
          <h2 className={styles.tapeTitle}>Your order</h2>
          <span className={styles.tapeSub}>
            {timeline.order ? `ORDER ${timeline.order.orderNumber} · PLACED` : `${live.session?.lane ?? ''} · IN PROGRESS`}
          </span>
        </div>
        <hr className={styles.rule} />

        {lines.length === 0 && !timeline.pending && <p className={styles.tapeEmpty}>NOTHING YET</p>}

        <ul className={styles.items}>
          {lines.map((line) => (
            <li key={line.line_no} className={styles.item}>
              <div className={styles.itemRow}>
                <span className={styles.itemName}>
                  {line.quantity} {line.item_name}
                </span>
                <span aria-hidden="true" className={styles.leader} />
                <span className={styles.amount}>{(line.line_total_cents / 100).toFixed(2)}</span>
              </div>
              <div className={styles.itemDetail}>
                {[`L${line.line_no}`, line.options.map((option) => option.option_name).join(' / ')].filter(Boolean).join(' · ')}
              </div>
            </li>
          ))}
        </ul>

        {timeline.pending && (
          <div className={styles.stamp}>
            <span className={styles.stampLabel}>Hold · not added yet</span>
            <span className={styles.stampItem}>
              {timeline.pending.quantity} {live.itemName(timeline.pending.itemCode)}
            </span>
            <span className={styles.stampNote}>Waiting for the customer to say yes.</span>
          </div>
        )}

        <hr className={`${styles.rule} ${styles.ruleBottom}`} />
        <dl className={styles.totals}>
          <dt>Subtotal</dt>
          <dd>{((cart?.subtotal_cents ?? 0) / 100).toFixed(2)}</dd>
          <dt>Tax</dt>
          <dd>{((cart?.tax_cents ?? 0) / 100).toFixed(2)}</dd>
          <dt className={styles.totalLabel}>Total</dt>
          <dd className={styles.totalValue}>{money(cart?.total_cents ?? 0)}</dd>
        </dl>
      </div>
    </aside>
  )
}
