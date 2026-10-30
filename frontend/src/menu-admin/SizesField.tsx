import { Field } from './Field'
import styles from './MenuAdmin.module.css'
import { MAX_SIZES, newSizeRow, parsePrice } from './words'
import type { SizeRow } from './words'

interface SizesFieldProps {
  sizes: SizeRow[]
  problem?: string
  /** null: back to one price, the cheapest size's. */
  onChange: (sizes: SizeRow[] | null, price?: string) => void
}

const ASK = 'ask'

/**
 * An item that comes in sizes, each at what a guest actually pays. The
 * backend turns those into the item's price and what each size adds.
 */
export function SizesField({ sizes, problem, onChange }: SizesFieldProps) {
  const defaultSize = sizes.find((size) => size.isDefault)?.key ?? ASK

  function change(key: string, fields: Partial<SizeRow>) {
    onChange(sizes.map((size) => (size.key === key ? { ...size, ...fields } : size)))
  }

  function chooseDefault(key: string) {
    onChange(sizes.map((size) => ({ ...size, isDefault: size.key === key })))
  }

  function backToOnePrice() {
    const priced = sizes.filter((size) => parsePrice(size.price) !== null)
    const cheapest = priced.sort((a, b) => (parsePrice(a.price) ?? 0) - (parsePrice(b.price) ?? 0))[0]
    onChange(null, cheapest?.price ?? '')
  }

  return (
    <div className={styles.sizes}>
      <span className={styles.label}>Sizes</span>
      <ul className={styles.sizeRows}>
        {sizes.map((size, index) => (
          <li key={size.key} className={styles.sizeRow}>
            <input
              aria-label={`Size ${index + 1} name`}
              placeholder={index === 0 ? 'Small' : index === 1 ? 'Large' : 'Size'}
              value={size.name}
              onChange={(event) => change(size.key, { name: event.target.value })}
              maxLength={60}
              autoComplete="off"
              autoFocus={index === 0 && !size.name}
            />
            <span className={styles.prefixed}>
              <span aria-hidden="true">$</span>
              <input
                aria-label={`Size ${index + 1} price`}
                inputMode="decimal"
                placeholder="0.00"
                value={size.price}
                onChange={(event) => change(size.key, { price: event.target.value })}
              />
            </span>
            {sizes.length > 2 ? (
              <button
                type="button"
                className={styles.sizeRemove}
                aria-label={`Remove ${size.name.trim() || `size ${index + 1}`}`}
                onClick={() => onChange(sizes.filter((each) => each.key !== size.key))}
              >
                <span aria-hidden="true">×</span>
              </button>
            ) : (
              <span />
            )}
          </li>
        ))}
      </ul>
      {problem && <p className={styles.fieldProblem}>{problem}</p>}
      <div className={styles.sizeLinks}>
        {sizes.length < MAX_SIZES && (
          <button type="button" className={styles.linkButton} onClick={() => onChange([...sizes, newSizeRow()])}>
            Add a size
          </button>
        )}
        <button type="button" className={styles.linkButton} onClick={backToOnePrice}>
          Use one price
        </button>
      </div>
      <div className={styles.sizeDefault}>
        <Field label="If a guest doesn’t say">
          {(props) => (
            <select {...props} value={defaultSize} onChange={(event) => chooseDefault(event.target.value)}>
              <option value={ASK}>The agent asks</option>
              {sizes.map((size, index) => (
                <option key={size.key} value={size.key}>
                  {size.name.trim() || `Size ${index + 1}`}
                </option>
              ))}
            </select>
          )}
        </Field>
      </div>
      <p className={styles.help}>Stock is counted per item, not per size.</p>
    </div>
  )
}
