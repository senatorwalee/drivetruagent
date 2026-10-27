import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import { api, type Caller, type CallerStatus } from '../api/client'
import { HomeLink } from '../app/HomeLink'
import { LIVE_PATH, PICKUPS_PATH, STAFF_PATH } from '../app/navigation'
import { PageLink } from '../app/PageLink'
import { useNow } from '../session/useNow'
import staff from '../staff/Staff.module.css'
import styles from './Callers.module.css'
import {
  ago,
  counted,
  describeProblem,
  maskedPhone,
  spacedPhone,
  staffName,
  standingLabel,
  standingReason,
  statusActions,
  statusEffect,
} from './words'

const REFRESH_MS = 15_000

type Editing = { kind: 'status'; callerId: string; status: CallerStatus } | { kind: 'delete'; callerId: string } | null

/**
 * Callers: every number that has called, what it has done, and what staff
 * decided about it. Numbers are hidden but for the last four digits until
 * someone asks to see one.
 */
export function CallersPage() {
  const queryClient = useQueryClient()
  const now = useNow()
  const [revealed, setRevealed] = useState<ReadonlySet<string>>(new Set())
  const [editing, setEditing] = useState<Editing>(null)
  const [reason, setReason] = useState('')

  const callers = useQuery({ queryKey: ['callers'], queryFn: api.callers, refetchInterval: REFRESH_MS, retry: false })
  const done = () => {
    setEditing(null)
    setReason('')
    return queryClient.invalidateQueries({ queryKey: ['callers'] })
  }
  const changing = useMutation({
    mutationFn: ({ callerId, status }: { callerId: string; status: CallerStatus }) =>
      api.setCallerStatus(callerId, status, reason.trim(), staffName()),
    onSuccess: done,
  })
  const deleting = useMutation({ mutationFn: api.deleteCaller, onSuccess: done })
  const busy = changing.isPending || deleting.isPending
  const problem = describeProblem(changing.error ?? deleting.error)
  const rows = callers.data ?? []

  const start = (next: Editing) => {
    changing.reset()
    deleting.reset()
    setReason('')
    setEditing(next)
  }

  const toggleNumber = (callerId: string) =>
    setRevealed((shown) => {
      const next = new Set(shown)
      if (next.has(callerId)) next.delete(callerId)
      else next.add(callerId)
      return next
    })

  const editor = (caller: Caller) => {
    if (editing?.callerId !== caller.caller_id) return null
    if (editing.kind === 'delete') {
      return (
        <div className={styles.editor} role="group" aria-label="Delete this number">
          <p className={styles.effect}>
            Delete this number and everything known about it: its calls, no-shows and status history. Its orders stay, linked to
            no one. This cannot be undone.
          </p>
          <div className={styles.actions}>
            <button type="button" className={`${staff.button} ${styles.danger}`} disabled={busy} onClick={() => deleting.mutate(caller.caller_id)}>
              Delete number
            </button>
            <button type="button" className={staff.button} disabled={busy} onClick={() => start(null)}>
              Keep it
            </button>
          </div>
        </div>
      )
    }
    const action = statusActions(caller.status).find((candidate) => candidate.status === editing.status)
    const submit = (event: FormEvent) => {
      event.preventDefault()
      if (reason.trim().length >= 3) changing.mutate({ callerId: caller.caller_id, status: editing.status })
    }
    return (
      <form className={styles.editor} onSubmit={submit} aria-label={`${action?.label ?? 'Change'} this number`}>
        <p className={styles.effect}>{statusEffect(editing.status)}</p>
        <label className={staff.field}>
          Reason (kept with the change)
          <input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} required minLength={3} autoFocus />
        </label>
        <div className={styles.actions}>
          <button type="submit" className={`${staff.button} ${staff.buttonPrimary}`} disabled={busy || reason.trim().length < 3}>
            {action?.label ?? 'Save'}
          </button>
          <button type="button" className={staff.button} disabled={busy} onClick={() => start(null)}>
            Cancel
          </button>
        </div>
      </form>
    )
  }

  return (
    <div className={staff.screen}>
      <header className={staff.topBar}>
        <div className={staff.titleGroup}>
          <HomeLink />
          <h1 className={staff.title}>
            Staff <span className={staff.titleSub}>Callers</span>
          </h1>
        </div>
        <nav className={staff.pageLinks} aria-label="Pages">
          <PageLink to={STAFF_PATH} className={staff.button}>
            Handoffs
          </PageLink>
          <PageLink to={PICKUPS_PATH} className={staff.button}>
            Pickups
          </PageLink>
          <PageLink to={LIVE_PATH} className={staff.button}>
            Live session
          </PageLink>
        </nav>
      </header>

      {callers.isError && (
        <p role="alert" className={staff.banner}>
          Cannot reach the backend. Start it with `make dev`, then reload.
        </p>
      )}

      <main className={styles.main}>
        <div className={styles.intro}>
          <h2 className={styles.heading}>Phone numbers that have called</h2>
          <p className={styles.lede}>
            A number with recent no-shows is flagged by itself, and its big orders go to a person. Flag or block a number yourself
            here; every change is kept with who made it and why. A number not heard from in a year is deleted.
          </p>
        </div>

        {problem && (
          <p role="alert" className={styles.problem}>
            {problem}
          </p>
        )}

        <section className={styles.panel} aria-labelledby="callers-title" aria-busy={callers.isLoading}>
          <h3 id="callers-title" className={styles.panelTitle}>
            Latest call first
          </h3>
          {callers.isLoading ? (
            <p className={styles.empty}>Loading…</p>
          ) : rows.length === 0 ? (
            <p className={styles.empty}>No calls yet. A number appears here after its first call.</p>
          ) : (
            <ul className={styles.list} aria-label="Callers">
              {rows.map((caller) => {
                const shown = revealed.has(caller.caller_id)
                const why = standingReason(caller)
                return (
                  <li key={caller.caller_id} className={styles.row} aria-label={`Number ending ${caller.phone_e164.slice(-4)}`}>
                    <div className={styles.rowMain}>
                      <span className={`${styles.rowTitle} ${styles.number}`}>{shown ? spacedPhone(caller.phone_e164) : maskedPhone(caller.phone_e164)}</span>
                      <button type="button" className={styles.reveal} onClick={() => toggleNumber(caller.caller_id)} aria-pressed={shown}>
                        {shown ? 'Hide' : 'Show number'}
                      </button>
                      <span className={styles.chip} data-tone={caller.standing}>
                        {standingLabel(caller.standing)}
                      </span>
                    </div>
                    {why && <p className={styles.rowDetail}>{why}</p>}
                    <p className={styles.rowMeta}>
                      {counted(caller.call_count, 'call')} · {counted(caller.order_count, 'order')} · {counted(caller.recent_no_shows, 'recent no-show')} ·{' '}
                      {counted(caller.recent_calls_without_order, 'recent call')} without an order · last call {ago(caller.last_call_at, now)}
                      {caller.history_opted_out && ' · asked not to keep their orders'}
                    </p>
                    {editing?.callerId === caller.caller_id ? (
                      editor(caller)
                    ) : (
                      <div className={styles.actions}>
                        {statusActions(caller.status).map((action) => (
                          <button
                            key={action.status}
                            type="button"
                            className={staff.button}
                            disabled={busy}
                            onClick={() => start({ kind: 'status', callerId: caller.caller_id, status: action.status })}
                          >
                            {action.label}
                          </button>
                        ))}
                        <button type="button" className={staff.button} disabled={busy} onClick={() => start({ kind: 'delete', callerId: caller.caller_id })}>
                          Delete
                        </button>
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      </main>
    </div>
  )
}
