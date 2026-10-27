import { HOME_PATH } from './navigation'
import styles from './HomeLink.module.css'
import { PageLink } from './PageLink'

/** The Frontdesk wordmark at the start of every top bar. It goes home. */
export function HomeLink() {
  return (
    <PageLink to={HOME_PATH} className={styles.home}>
      <FrontdeskMark className={styles.mark} />
      <span>Frontdesk</span>
    </PageLink>
  )
}

/** The mark: the agent's orb, as on the live screen. */
export function FrontdeskMark({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="var(--raised)" />
      <circle cx="16" cy="16" r="11" fill="none" stroke="var(--accent)" strokeWidth="2" opacity=".45" />
      <circle cx="16" cy="16" r="6" fill="var(--accent)" />
    </svg>
  )
}
