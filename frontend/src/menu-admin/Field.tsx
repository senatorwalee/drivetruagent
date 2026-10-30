import { useId } from 'react'
import type { ReactNode } from 'react'

import styles from './MenuAdmin.module.css'

interface FieldProps {
  label: string
  hint?: string
  problem?: string
  children: (props: { id: string; 'aria-describedby'?: string; 'aria-invalid'?: boolean }) => ReactNode
}

/** A labelled input, with its hint and what is wrong with it. */
export function Field({ label, hint, problem, children }: FieldProps) {
  const id = useId()
  const describedBy = [hint && `${id}-hint`, problem && `${id}-problem`].filter(Boolean).join(' ') || undefined
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      {children({ id, 'aria-describedby': describedBy, 'aria-invalid': problem ? true : undefined })}
      {hint && (
        <span id={`${id}-hint`} className={styles.help}>
          {hint}
        </span>
      )}
      {problem && (
        <span id={`${id}-problem`} className={styles.fieldProblem}>
          {problem}
        </span>
      )}
    </div>
  )
}
