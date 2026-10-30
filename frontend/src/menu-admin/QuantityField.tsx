import { Field } from './Field'
import styles from './MenuAdmin.module.css'
import { Segmented } from './Segmented'
import type { StockMode } from './words'

interface QuantityFieldProps {
  mode: StockMode
  stock: string
  problem?: string
  /** For an item already on the menu. A new one has nothing to run out of yet. */
  offerSoldOut?: boolean
  onMode: (mode: StockMode) => void
  onStock: (stock: string) => void
}

const WHAT_IT_MEANS: Record<StockMode, string> = {
  unlimited: 'Always available, until you mark it sold out.',
  counted: 'Goes down with every order, and sells out at zero.',
  sold_out: 'Guests and the agent are told it is gone, until you change this.',
}

/** How much of an item there is: unlimited, or an actual count. */
export function QuantityField({ mode, stock, problem, offerSoldOut, onMode, onStock }: QuantityFieldProps) {
  const options: { value: StockMode; label: string }[] = [
    { value: 'unlimited', label: 'Unlimited' },
    { value: 'counted', label: 'Counted' },
  ]
  if (offerSoldOut) options.push({ value: 'sold_out', label: 'Sold out' })

  return (
    <div className={styles.quantity}>
      <span className={styles.label}>Quantity</span>
      <Segmented label="Quantity" options={options} value={mode} onChange={onMode} />
      <p className={styles.help}>{WHAT_IT_MEANS[mode]}</p>
      {mode === 'counted' && (
        <Field label="How many do you have?" problem={problem}>
          {(props) => <input {...props} inputMode="numeric" value={stock} onChange={(event) => onStock(event.target.value)} />}
        </Field>
      )}
    </div>
  )
}
