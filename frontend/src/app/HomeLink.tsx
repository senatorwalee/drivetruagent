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

/**
 * The mark: a service bell on a counter, ringing. The bell and counter take
 * the text colour and the sound the accent, so it follows the theme.
 */
export function FrontdeskMark({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 64 64" aria-hidden="true">
      <g fill="none" stroke="var(--accent)" strokeWidth="3.5" strokeLinecap="round">
        <path d="M22.1 12.1a14 14 0 0 1 19.8 0" />
        <path d="M26.34 16.34a8 8 0 0 1 11.32 0" />
      </g>
      <circle cx="32" cy="22" r="3.8" fill="var(--accent)" />
      <path d="M13 46a19 19 0 0 1 38 0z" fill="var(--text)" />
      <rect x="8" y="49" width="48" height="5" rx="2.5" fill="var(--text)" />
    </svg>
  )
}
