import type { ReactNode } from 'react'

import type { Allergen } from '../api/client'
import { ALLERGENS, allergenLabel, levelOf, NONE_OF_THESE, reviewLabel, withLevel, withReason } from './allergens'
import type { AllergenLevel, Review } from './allergens'
import styles from './MenuAdmin.module.css'
import { Segmented } from './Segmented'

interface AllergenFieldProps {
  itemName: string
  /** Shown beside the choice: the item's name, or a label. */
  title: ReactNode
  /** null: not reviewed. */
  review: Review | null
  /** Whether the list of allergens is open. */
  listing: boolean
  onListing: (listing: boolean) => void
  onChange: (review: Review | null) => void
}

type Answer = 'unreviewed' | 'none' | 'some'

const ANSWERS: { value: Answer; label: string }[] = [
  { value: 'unreviewed', label: 'Not reviewed' },
  { value: 'none', label: 'None of these' },
  { value: 'some', label: 'Has allergens' },
]

/**
 * The review of one item. Each allergen is picked from the twelve, never
 * typed. Choosing "Has allergens" only opens the list: nothing is reviewed
 * until something is ticked, so leaving it empty leaves the item not reviewed.
 */
export function AllergenField({ itemName, title, review, listing, onListing, onChange }: AllergenFieldProps) {
  const answer: Answer = review?.noneOfThese ? 'none' : review || listing ? 'some' : 'unreviewed'

  function choose(next: Answer) {
    if (next === 'some') {
      if (review?.noneOfThese) onChange(null)
      onListing(true)
      return
    }
    onChange(next === 'none' ? NONE_OF_THESE : null)
    onListing(false)
  }

  return (
    <>
      <div className={styles.allergenHead}>
        {title}
        <Segmented label={`Allergens in ${itemName}`} options={ANSWERS} value={answer} onChange={choose} />
      </div>

      {review && !review.noneOfThese && !listing && (
        <p className={styles.allergenSummary}>
          {reviewLabel(review)}
          <button type="button" className={styles.linkButton} aria-label={`Change allergens in ${itemName}`} onClick={() => onListing(true)}>
            Change
          </button>
        </p>
      )}

      {listing && (
        <div className={styles.allergenList}>
          <Level title="Contains" hint="It is an ingredient." level="contains" itemName={itemName} review={review} onChange={onChange} />
          <Level title="May contain" hint="Not an ingredient, but it could get in." level="may_contain" itemName={itemName} review={review} onChange={onChange} />
          {review?.mayContain.map(({ allergen, because }) => (
            <input
              key={allergen}
              className={styles.allergenReason}
              aria-label={`Why ${itemName} may contain ${allergenLabel(allergen).toLowerCase()}`}
              placeholder={`Why ${allergenLabel(allergen).toLowerCase()}, in words a guest can hear`}
              value={because}
              onChange={(event) => onChange(withReason(review, allergen, event.target.value))}
              maxLength={160}
            />
          ))}
          <div className={styles.allergenDone}>
            <button type="button" className={styles.linkButton} aria-label={`Done with allergens in ${itemName}`} onClick={() => onListing(false)}>
              Done
            </button>
            {!review && <span className={styles.help}>Tick at least one, or it stays not reviewed.</span>}
          </div>
        </div>
      )}
    </>
  )
}

interface LevelProps {
  title: string
  hint: string
  level: AllergenLevel
  itemName: string
  review: Review | null
  onChange: (review: Review | null) => void
}

function Level({ title, hint, level, itemName, review, onChange }: LevelProps) {
  const toggle = (allergen: Allergen) => onChange(withLevel(review, allergen, levelOf(review, allergen) === level ? null : level))

  return (
    <div className={styles.allergenLevel}>
      <span className={styles.label}>
        {title} <span className={styles.help}>{hint}</span>
      </span>
      <div className={styles.chips} role="group" aria-label={`${itemName}: ${title.toLowerCase()}`}>
        {ALLERGENS.map(({ id, label }) => (
          <button key={id} type="button" className={styles.chip} aria-pressed={levelOf(review, id) === level} onClick={() => toggle(id)}>
            {label}
          </button>
        ))}
      </div>
    </div>
  )
}
