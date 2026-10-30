import type { OnboardingResult } from '../api/client'
import { LIVE_PATH, MENU_PATH } from '../app/navigation'
import { PageLink } from '../app/PageLink'
import staff from '../staff/Staff.module.css'
import styles from './Onboarding.module.css'
import { setupSummary, unreviewedNote } from './words'

/** The end: the restaurant is on, and what to do next. */
export function DoneScreen({ result }: { result: OnboardingResult }) {
  return (
    <main className={styles.done}>
      <span className={styles.doneMark} aria-hidden="true">
        ✓
      </span>
      <h2 className={styles.doneTitle}>{result.restaurant_name} is live.</h2>
      <p className={styles.doneText}>The agent has {setupSummary(result)}, from the next order.</p>
      {result.allergen_reviews < result.items && (
        <p className={styles.doneNote}>
          {unreviewedNote(result.items - result.allergen_reviews)} Review them on the Menu page.
        </p>
      )}
      {result.items_taken_off > 0 && (
        <p className={styles.doneNote}>
          {result.items_taken_off} {result.items_taken_off === 1 ? 'item' : 'items'} moved to Taken off on the Menu page.
        </p>
      )}
      <div className={styles.doneActions}>
        <PageLink to={LIVE_PATH} className={`${staff.button} ${staff.buttonPrimary}`}>
          Try it in the live lane
        </PageLink>
        <PageLink to={MENU_PATH} className={staff.button}>
          Add photos on the Menu page
        </PageLink>
      </div>
    </main>
  )
}
