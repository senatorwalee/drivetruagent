import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../api/client'
import { HomeLink } from '../app/HomeLink'
import { LIVE_PATH, MENU_PATH, STAFF_PATH } from '../app/navigation'
import { PageLink } from '../app/PageLink'
import { useNow } from '../session/useNow'
import staff from '../staff/Staff.module.css'
import styles from './Insights.module.css'
import { lastAsked, matchLabel, share, suggestion, timesLabel } from './words'

const PERIODS = [
  { days: 7, label: 'Last 7 days' },
  { days: 30, label: 'Last 30 days' },
  { days: 90, label: 'Last 90 days' },
] as const

// New requests arrive as guests order; a manager glancing at the page
// should see them without reloading.
const REFRESH_MS = 30_000

/**
 * Insights: what guests are telling the restaurant. For now, what they asked
 * for that the menu couldn't fill, counted from every conversation.
 */
export function InsightsPage() {
  const [days, setDays] = useState<number>(7)
  const now = useNow()
  const requests = useQuery({
    queryKey: ['unmet-requests', days],
    queryFn: () => api.unmetRequests(days),
    refetchInterval: REFRESH_MS,
    retry: false,
  })
  const rows = requests.data ?? []
  const most = rows[0]?.times ?? 0
  const period = PERIODS.find((p) => p.days === days)?.label.toLowerCase() ?? `last ${days} days`

  return (
    <div className={staff.screen}>
      <header className={staff.topBar}>
        <div className={staff.titleGroup}>
          <HomeLink />
          <h1 className={staff.title}>
            Insights <span className={staff.titleSub}>What guests ask for</span>
          </h1>
        </div>
        <nav className={styles.links} aria-label="Pages">
          <PageLink to={STAFF_PATH} className={staff.button}>
            Staff
          </PageLink>
          <PageLink to={MENU_PATH} className={staff.button}>
            Menu
          </PageLink>
          <PageLink to={LIVE_PATH} className={staff.button}>
            Live session
          </PageLink>
        </nav>
      </header>

      {requests.isError && (
        <p role="alert" className={staff.banner}>
          Cannot reach the backend. Start it with `make dev`, then reload.
        </p>
      )}

      <main className={styles.main}>
        <div className={styles.intro}>
          <h2 className={styles.heading}>Asked for, not on the menu</h2>
          <p className={styles.lede}>
            Every time a guest asks for something the menu doesn't have exactly, it is counted here. It shows what to add, and
            what to tell guests isn't available.
          </p>
        </div>

        <fieldset className={styles.periods}>
          <legend>Period</legend>
          {PERIODS.map((p) => (
            <label key={p.days} className={styles.period}>
              <input type="radio" name="period" value={p.days} checked={days === p.days} onChange={() => setDays(p.days)} />
              {p.label}
            </label>
          ))}
        </fieldset>

        <section className={styles.panel} aria-labelledby="unmet-title" aria-busy={requests.isLoading}>
          <div className={styles.panelHead}>
            <h3 id="unmet-title" className={styles.panelTitle}>
              Most asked, {period}
            </h3>
            <span className={styles.panelNote}>Updates every 30 seconds</span>
          </div>

          {requests.isLoading ? (
            <p className={styles.empty}>Loading…</p>
          ) : rows.length === 0 ? (
            <p className={styles.empty}>
              Nothing yet for the {period}. When a guest asks for something the menu doesn't have, it will appear here.
            </p>
          ) : (
            <>
              <p className={styles.suggestion}>{suggestion(rows[0])}</p>
              <ol className={styles.list} aria-label="Requests, most asked first">
                {rows.map((row) => (
                  <li key={row.request} className={styles.row}>
                    <span className={styles.request}>{row.request}</span>
                    <span className={styles.count}>{timesLabel(row.times)}</span>
                    <span className={styles.bar} aria-hidden="true">
                      <span className={styles.barFill} style={{ width: `${share(row.times, most)}%` }} />
                    </span>
                    <span className={styles.meta}>
                      <span className={`${styles.tag} ${row.match === 'close' ? styles.tagClose : styles.tagNone}`}>
                        {matchLabel(row.match)}
                      </span>
                      <span>last asked {lastAsked(row.last_asked, now)}</span>
                    </span>
                  </li>
                ))}
              </ol>
            </>
          )}
        </section>
      </main>
    </div>
  )
}
