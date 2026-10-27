import { HomeLink } from '../app/HomeLink'
import { CALLERS_PATH, CALLS_PATH, CONVERSATIONS_PATH, INSIGHTS_PATH, LIVE_PATH, MENU_PATH, PICKUPS_PATH } from '../app/navigation'
import { PageLink } from '../app/PageLink'
import { HandoffQueue } from './HandoffQueue'
import { PackageView } from './PackageView'
import styles from './Staff.module.css'
import { useStaffDesk } from './useStaffDesk'

/**
 * The staff screen: sessions the agent handed to a person, and what each one
 * needs. A person here does not join the conversation or edit the cart; they
 * read the package, say they have it, and close it.
 */
export function StaffPage() {
  const desk = useStaffDesk()

  return (
    <div className={styles.screen}>
      <header className={styles.topBar}>
        <div className={styles.titleGroup}>
          <HomeLink />
          <h1 className={styles.title}>
            Staff <span className={styles.titleSub}>Handoffs</span>
          </h1>
        </div>
        <nav className={styles.pageLinks} aria-label="Pages">
          <PageLink to={PICKUPS_PATH} className={styles.button}>
            Pickups
          </PageLink>
          <PageLink to={CALLERS_PATH} className={styles.button}>
            Callers
          </PageLink>
          <PageLink to={CONVERSATIONS_PATH} className={styles.button}>
            Conversations
          </PageLink>
          <PageLink to={INSIGHTS_PATH} className={styles.button}>
            Insights
          </PageLink>
          <PageLink to={MENU_PATH} className={styles.button}>
            Menu
          </PageLink>
          <PageLink to={LIVE_PATH} className={styles.button}>
            Live session
          </PageLink>
          <PageLink to={CALLS_PATH} className={styles.button}>
            Phone calls
          </PageLink>
        </nav>
      </header>

      {desk.status === 'failed' && (
        <p role="alert" className={styles.banner}>
          Cannot reach the backend. Start it with `make dev`, then reload.
        </p>
      )}

      <main className={styles.main}>
        <HandoffQueue desk={desk} />
        {desk.selected ? (
          <PackageView desk={desk} handoff={desk.selected} />
        ) : (
          <p className={styles.placeholder}>{desk.selectedId ? 'Loading…' : 'Select a handoff to see what it needs.'}</p>
        )}
      </main>
    </div>
  )
}
