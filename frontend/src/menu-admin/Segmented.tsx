import { useId } from 'react'

import styles from './MenuAdmin.module.css'

interface SegmentedProps<T extends string> {
  /** What is being chosen, for a screen reader. */
  label: string
  options: { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
}

/** One choice out of a few, all in view. Real radio buttons, so arrow keys work. */
export function Segmented<T extends string>({ label, options, value, onChange }: SegmentedProps<T>) {
  const name = useId()
  return (
    <div role="radiogroup" aria-label={label} className={styles.segmented}>
      {options.map((option) => (
        <label key={option.value} className={styles.segment} data-checked={option.value === value}>
          <input type="radio" name={name} value={option.value} checked={option.value === value} onChange={() => onChange(option.value)} />
          {option.label}
        </label>
      ))}
    </div>
  )
}
