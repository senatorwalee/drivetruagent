import type { ReactNode } from 'react'

import { money } from '../session/presentation'
import styles from './Onboarding.module.css'
import { allergenCounts, cheapestSet, comboParts, dietCounts, DIETS, itemsBySection } from './words'
import type { Draft, MenuToday, Step } from './words'

interface ReviewStepProps {
  draft: Draft
  today: MenuToday
  blockers: { step: Step; label: string; message: string }[]
  goTo: (step: Step) => void
}

/** Everything on one screen, each part a click away from being changed. */
export function ReviewStep({ draft, today, blockers, goTo }: ReviewStepProps) {
  const groups = itemsBySection(draft, today)
  const counted = draft.items.filter((item) => item.stock !== null).length
  const allergens = allergenCounts(draft)

  return (
    <div className={styles.review}>
      {blockers.length > 0 && (
        <ul className={styles.blockers} aria-label="Before you can go live">
          {blockers.map((blocker) => (
            <li key={blocker.step}>
              <span>{blocker.message}</span>
              <button type="button" className={styles.quiet} onClick={() => goTo(blocker.step)}>
                Go to {blocker.label}
              </button>
            </li>
          ))}
        </ul>
      )}

      <Part title="Restaurant" onEdit={() => goTo('restaurant')}>
        <p className={styles.reviewLead}>{draft.restaurant.name || 'No name yet'}</p>
        <p>{draft.restaurant.address || 'No address'}</p>
        <p>Pickup orders ready in {draft.restaurant.pickupMinutes} minutes</p>
      </Part>

      <Part title="Menu" onEdit={() => goTo('menu')}>
        <p className={styles.reviewLead}>
          {draft.items.length} {draft.items.length === 1 ? 'item' : 'items'}
        </p>
        <p>{groups.map((group) => `${group.section} ${group.items.length}`).join(' · ') || 'Nothing entered yet'}</p>
        {draft.items.length > 0 && (
          <p>
            {draft.items.length - counted} unlimited, {counted} counted
          </p>
        )}
      </Part>

      <Part title="Allergens" onEdit={() => goTo('allergens')}>
        <p className={styles.reviewLead}>
          {allergens.reviewed} of {draft.items.length} reviewed
        </p>
        {allergens.unreviewed > 0 && <p>The agent hands allergen questions about the others to a person.</p>}
      </Part>

      <Part title="Halal and kosher" onEdit={() => goTo('diets')}>
        <dl className={styles.dietSummary}>
          {DIETS.map((diet) => {
            const counts = dietCounts(draft, diet.id)
            return (
              <div key={diet.id}>
                <dt>{diet.label}</dt>
                <dd>
                  {counts.unreviewed === draft.items.length
                    ? 'Not reviewed. The agent makes no claims.'
                    : `${counts.certified} certified, ${counts.suitable} suitable, ${counts.not_suitable} not suitable, ${counts.unreviewed} not reviewed`}
                </dd>
              </div>
            )
          })}
        </dl>
      </Part>

      <Part title="Combos" onEdit={() => goTo('combos')}>
        {draft.combos.length === 0 ? (
          <p>No combos</p>
        ) : (
          <ul className={styles.reviewCombos}>
            {draft.combos.map((combo) => {
              const total = cheapestSet(comboParts(combo.slots, draft, today))
              return (
                <li key={combo.id}>
                  <strong>{combo.name}</strong>: {combo.slots.join(' + ')}, saves {money(combo.savingCents)}, from {money(Math.max(total - combo.savingCents, 0))}
                </li>
              )
            })}
          </ul>
        )}
      </Part>

      {draft.replaceMenu && today.items.length > 0 && (
        <p className={styles.reviewWarning}>
          <strong>
            This replaces your menu. The {today.items.length} {today.items.length === 1 ? 'item' : 'items'} on it now move to Taken off on the Menu page
            {today.comboNames.length > 0 && `, and ${today.comboNames.length === 1 ? 'its combo stops' : `its ${today.comboNames.length} combos stop`} applying`}.
          </strong>{' '}
          Nothing is deleted.
        </p>
      )}
      <p className={styles.reviewNote}>Photos can be added on the Menu page.</p>
    </div>
  )
}

function Part({ title, onEdit, children }: { title: string; onEdit: () => void; children: ReactNode }) {
  return (
    <section className={styles.reviewPart} aria-label={title}>
      <div className={styles.reviewHead}>
        <h3>{title}</h3>
        <button type="button" className={styles.quiet} aria-label={`Change ${title}`} onClick={onEdit}>
          Change
        </button>
      </div>
      {children}
    </section>
  )
}
