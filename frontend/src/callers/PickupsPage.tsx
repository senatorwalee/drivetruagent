import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, type Pickup, type PickupMark } from '../api/client'
import { CALLERS_PATH, LIVE_PATH, STAFF_PATH } from '../app/navigation'
import { PageLink } from '../app/PageLink'
import { money } from '../session/presentation'
import { useNow } from '../session/useNow'
import staff from '../staff/Staff.module.css'
import styles from './Callers.module.css'
import { describeProblem, maskedPhone, pickupLines, pickupStatusLabel, placedAgo, staffName } from './words'

// A phone order placed a moment ago should be on the counter's screen
// without anyone reloading.
const REFRESH_MS = 5000

/**
 * Pickups: phone orders waiting at the counter. Staff mark each one picked up
 * or a no-show; nothing else ever marks a no-show.
 */
export function PickupsPage() {
  const queryClient = useQueryClient()
  const now = useNow()
  const pickups = useQuery({ queryKey: ['pickups'], queryFn: api.pickups, refetchInterval: REFRESH_MS, retry: false })
  const marking = useMutation({
    mutationFn: ({ orderId, status }: { orderId: string; status: PickupMark }) => api.markPickup(orderId, status, staffName()),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['pickups'] }),
  })
  const rows = pickups.data ?? []
  const waiting = rows.filter((pickup) => pickup.status === 'waiting')
  const marked = rows.filter((pickup) => pickup.status !== 'waiting')
  const problem = describeProblem(marking.error)

  const actions = (pickup: Pickup) => {
    const mark = (status: PickupMark) => marking.mutate({ orderId: pickup.order_id, status })
    const label = `order ${pickup.order_number}`
    if (pickup.status === 'waiting') {
      return (
        <div className={styles.actions}>
          <button type="button" className={`${staff.button} ${staff.buttonPrimary}`} disabled={marking.isPending} onClick={() => mark('picked_up')} aria-label={`Picked up, ${label}`}>
            Picked up
          </button>
          <button type="button" className={staff.button} disabled={marking.isPending} onClick={() => mark('no_show')} aria-label={`No-show, ${label}`}>
            No-show
          </button>
        </div>
      )
    }
    // A wrong mark can be put right: someone marked absent may turn up late.
    const other: PickupMark = pickup.status === 'picked_up' ? 'no_show' : 'picked_up'
    return (
      <div className={styles.actions}>
        <button type="button" className={staff.button} disabled={marking.isPending} onClick={() => mark(other)} aria-label={`Change to ${other === 'picked_up' ? 'picked up' : 'no-show'}, ${label}`}>
          {other === 'picked_up' ? 'Picked up after all' : 'Was a no-show'}
        </button>
      </div>
    )
  }

  const row = (pickup: Pickup) => (
    <li key={pickup.order_id} className={styles.row} data-status={pickup.status}>
      <div className={styles.rowMain}>
        <span className={styles.rowTitle}>Order {pickup.order_number}</span>
        <span className={styles.chip} data-tone={pickup.status}>
          {pickupStatusLabel(pickup, now)}
        </span>
      </div>
      <p className={styles.rowDetail}>{pickupLines(pickup)}</p>
      <p className={styles.rowMeta}>
        {money(pickup.total_cents)} · {pickup.caller_last_four ? maskedPhone(pickup.caller_last_four) : 'number withheld'} · {placedAgo(pickup, now)}
      </p>
      {actions(pickup)}
    </li>
  )

  return (
    <div className={staff.screen}>
      <header className={staff.topBar}>
        <h1 className={staff.title}>
          Staff <span className={staff.titleSub}>Pickups</span>
        </h1>
        <nav className={staff.pageLinks} aria-label="Pages">
          <PageLink to={STAFF_PATH} className={staff.button}>
            Handoffs
          </PageLink>
          <PageLink to={CALLERS_PATH} className={staff.button}>
            Callers
          </PageLink>
          <PageLink to={LIVE_PATH} className={staff.button}>
            Live session
          </PageLink>
        </nav>
      </header>

      {pickups.isError && (
        <p role="alert" className={staff.banner}>
          Cannot reach the backend. Start it with `make dev`, then reload.
        </p>
      )}

      <main className={styles.main}>
        <div className={styles.intro}>
          <h2 className={styles.heading}>Phone orders to collect</h2>
          <p className={styles.lede}>
            Phone orders are paid at pickup. Mark each one when it is collected. If nobody comes, mark it a no-show: two no-shows
            from a number means its big orders go to a person first.
          </p>
        </div>

        {problem && (
          <p role="alert" className={styles.problem}>
            {problem}
          </p>
        )}

        <section className={styles.panel} aria-labelledby="waiting-title" aria-busy={pickups.isLoading}>
          <h3 id="waiting-title" className={styles.panelTitle}>
            Waiting <span className={staff.count}>{waiting.length}</span>
          </h3>
          {pickups.isLoading ? (
            <p className={styles.empty}>Loading…</p>
          ) : waiting.length === 0 ? (
            <p className={styles.empty}>Nothing waiting. A phone order will appear here as soon as it is placed.</p>
          ) : (
            <ul className={styles.list} aria-label="Waiting, oldest first">
              {waiting.map(row)}
            </ul>
          )}
        </section>

        {marked.length > 0 && (
          <section className={styles.panel} aria-labelledby="marked-title">
            <h3 id="marked-title" className={styles.panelTitle}>
              Marked in the last 12 hours
            </h3>
            <ul className={styles.list} aria-label="Marked, newest first">
              {marked.map(row)}
            </ul>
          </section>
        )}
      </main>
    </div>
  )
}
