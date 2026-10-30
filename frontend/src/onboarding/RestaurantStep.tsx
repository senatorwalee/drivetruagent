import type { FormEvent } from 'react'

import { Field } from '../menu-admin/Field'
import styles from './Onboarding.module.css'
import type { Draft } from './words'

interface RestaurantStepProps {
  draft: Draft
  update: (change: Partial<Draft>) => void
  onDone: () => void
}

/** Who the restaurant is. Each answer changes what the agent says. */
export function RestaurantStep({ draft, update, onDone }: RestaurantStepProps) {
  const { restaurant } = draft
  const edit = (field: keyof Draft['restaurant']) => (value: string) => update({ restaurant: { ...restaurant, [field]: value } })

  function submit(event: FormEvent) {
    event.preventDefault()
    onDone()
  }

  return (
    <form className={styles.narrow} onSubmit={submit} noValidate>
      <Field label="Restaurant name" hint="The agent answers with this name.">
        {(props) => <input {...props} value={restaurant.name} onChange={(e) => edit('name')(e.target.value)} maxLength={80} autoComplete="off" required autoFocus />}
      </Field>
      <Field label="Address" hint="Optional. The agent tells phone callers where to pick up.">
        {(props) => <input {...props} value={restaurant.address} onChange={(e) => edit('address')(e.target.value)} maxLength={160} autoComplete="off" />}
      </Field>
      <Field label="Pickup time" hint="How long a phone order takes to be ready.">
        {(props) => (
          <span className={styles.suffixed}>
            <input {...props} inputMode="numeric" value={restaurant.pickupMinutes} onChange={(e) => edit('pickupMinutes')(e.target.value)} />
            <span aria-hidden="true">minutes</span>
          </span>
        )}
      </Field>
      {/* Enter moves on, as it does anywhere else in a form. */}
      <button type="submit" hidden />
    </form>
  )
}
