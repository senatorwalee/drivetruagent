import { useState } from 'react'

import type { Diet, DietStatus } from '../api/client'
import menuStyles from '../menu-admin/MenuAdmin.module.css'
import { Segmented } from '../menu-admin/Segmented'
import staff from '../staff/Staff.module.css'
import styles from './Onboarding.module.css'
import { dietCounts, DIETS, itemsBySection, reviewSection, sectionKey, withReview } from './words'
import type { Draft, DraftItem, MenuToday } from './words'

interface DietStepProps {
  draft: Draft
  today: MenuToday
  update: (change: Partial<Draft>) => void
}

type Choice = DietStatus | 'none'

const CHOICES: { value: Choice; label: string }[] = [
  { value: 'none', label: 'Not reviewed' },
  { value: 'not_suitable', label: 'Not suitable' },
  { value: 'suitable', label: 'Suitable' },
  { value: 'certified', label: 'Certified' },
]

/**
 * What the restaurant can say for each diet, one diet at a time. The words
 * are the rule: suitable is not the same claim as certified, and the agent
 * never turns one into the other.
 */
export function DietStep({ draft, today, update }: DietStepProps) {
  const [diet, setDiet] = useState<Diet>('halal')
  const name = DIETS.find((each) => each.id === diet)?.label.toLowerCase() ?? diet
  const groups = itemsBySection(draft, today)

  function setItem(next: DraftItem) {
    update({ items: draft.items.map((item) => (item.id === next.id ? next : item)) })
  }

  if (draft.items.length === 0) return <p className={styles.emptyList}>Add your menu first, then come back to this.</p>

  return (
    <>
      <div className={staff.tabs} role="group" aria-label="Diet">
        {DIETS.map((each) => {
          const counts = dietCounts(draft, each.id)
          return (
            <button key={each.id} type="button" className={staff.tab} aria-pressed={diet === each.id} onClick={() => setDiet(each.id)}>
              {each.label}
              <span className={staff.count}>
                {draft.items.length - counts.unreviewed} of {draft.items.length}
              </span>
            </button>
          )
        })}
      </div>

      <dl className={styles.legend}>
        <dt>Certified</dt>
        <dd>A recognised body certified it. Only these are called {name}.</dd>
        <dt>Suitable</dt>
        <dd>
          Nothing {name} rules forbid, prepared apart, no certificate. The agent says “suitable for guests eating {name}, not certified”.
        </dd>
        <dt>Not reviewed</dt>
        <dd>The agent says it can’t confirm it either way.</dd>
      </dl>

      {groups.map((group) => (
        <section key={sectionKey(group.section)} className={styles.dietGroup} aria-label={group.section}>
          <div className={styles.dietGroupHead}>
            <h3 className={menuStyles.groupTitle}>
              {group.section} <span className={menuStyles.groupCount}>{group.items.length}</span>
            </h3>
            <div className={styles.setAll}>
              <span>All:</span>
              <button type="button" className={styles.quiet} onClick={() => update({ items: reviewSection(draft.items, group.section, diet, 'suitable') })}>
                Suitable
              </button>
              <button type="button" className={styles.quiet} onClick={() => update({ items: reviewSection(draft.items, group.section, diet, 'not_suitable') })}>
                Not suitable
              </button>
            </div>
          </div>
          <ul className={styles.rows}>
            {group.items.map((item) => (
              <DietRow key={item.id} item={item} diet={diet} dietName={name} onChange={setItem} />
            ))}
          </ul>
        </section>
      ))}
    </>
  )
}

interface DietRowProps {
  item: DraftItem
  diet: Diet
  dietName: string
  onChange: (item: DraftItem) => void
}

function DietRow({ item, diet, dietName, onChange }: DietRowProps) {
  const review = item.diets[diet]
  const [giveReason, setGiveReason] = useState(false)
  const showReason = review !== undefined && review.status !== 'certified' && (giveReason || review.note !== '')

  function choose(choice: Choice) {
    onChange(withReview(item, diet, choice === 'none' ? null : { status: choice, note: review?.note ?? '', certifiedBy: review?.certifiedBy ?? '' }))
  }

  return (
    <li className={styles.dietRow}>
      <div className={styles.dietChoice}>
        <span className={styles.dietName}>
          <span className={styles.itemName}>{item.name_en}</span>
          {review && review.status !== 'certified' && !showReason && (
            <button type="button" className={styles.quiet} onClick={() => setGiveReason(true)}>
              Add a reason
            </button>
          )}
        </span>
        <Segmented label={`${item.name_en} for ${dietName}`} options={CHOICES} value={review?.status ?? 'none'} onChange={choose} />
      </div>
      {review?.status === 'certified' && (
        <input
          className={styles.dietInput}
          aria-label={`Who certified ${item.name_en}`}
          placeholder="Certified by"
          value={review.certifiedBy}
          onChange={(event) => onChange(withReview(item, diet, { ...review, certifiedBy: event.target.value }))}
          maxLength={80}
        />
      )}
      {review && showReason && (
        <input
          className={styles.dietInput}
          aria-label={`Why, for ${item.name_en}`}
          placeholder={review.status === 'suitable' ? 'Why, in words a guest can hear: Fried in its own oil' : 'Why, in words a guest can hear: Our beef isn’t certified'}
          value={review.note}
          onChange={(event) => onChange(withReview(item, diet, { ...review, note: event.target.value }))}
          maxLength={160}
          autoFocus={giveReason}
        />
      )}
    </li>
  )
}
