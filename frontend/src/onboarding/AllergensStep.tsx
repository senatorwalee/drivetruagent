import { useState } from 'react'

import { AllergenField } from '../menu-admin/AllergenField'
import menuStyles from '../menu-admin/MenuAdmin.module.css'
import styles from './Onboarding.module.css'
import { allergenCounts, itemsBySection, sectionKey } from './words'
import type { Draft, DraftItem, MenuToday } from './words'

interface AllergensStepProps {
  draft: Draft
  today: MenuToday
  update: (change: Partial<Draft>) => void
}

/**
 * What each item contains, one item at a time. There is no "all" button as
 * the halal step has: saying an item has none of them is what offers it to a
 * guest with an allergy, so it is said once per item, on purpose.
 */
export function AllergensStep({ draft, today, update }: AllergensStepProps) {
  const [listing, setListing] = useState<string | null>(null)
  const counts = allergenCounts(draft)

  function setItem(next: DraftItem) {
    update({ items: draft.items.map((item) => (item.id === next.id ? next : item)) })
  }

  if (draft.items.length === 0) return <p className={styles.emptyList}>Add your menu first, then come back to this.</p>

  return (
    <>
      <p className={styles.progress}>
        {counts.reviewed} of {draft.items.length} reviewed
      </p>

      <dl className={styles.legend}>
        <dt>Not reviewed</dt>
        <dd>The agent says nothing about it and hands the question to a person.</dd>
        <dt>None of these</dt>
        <dd>You checked, and it has none of the twelve. The agent can offer it to a guest avoiding one.</dd>
        <dt>Has allergens</dt>
        <dd>Pick what it contains, and what may get in and why. The agent reads that out.</dd>
      </dl>

      {itemsBySection(draft, today).map((group) => (
        <section key={sectionKey(group.section)} className={styles.dietGroup} aria-label={group.section}>
          <h3 className={menuStyles.groupTitle}>
            {group.section} <span className={menuStyles.groupCount}>{group.items.length}</span>
          </h3>
          <ul className={styles.rows}>
            {group.items.map((item) => (
              <li key={item.id} className={styles.dietRow}>
                <AllergenField
                  itemName={item.name_en}
                  title={<span className={styles.itemName}>{item.name_en}</span>}
                  review={item.allergens}
                  listing={listing === item.id}
                  onListing={(open) => setListing(open ? item.id : null)}
                  onChange={(allergens) => setItem({ ...item, allergens })}
                />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  )
}
