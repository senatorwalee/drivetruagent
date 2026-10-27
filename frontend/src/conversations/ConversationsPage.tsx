import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api, type ConversationSummary } from '../api/client'
import { HomeLink } from '../app/HomeLink'
import { CALLERS_PATH, LIVE_PATH, PICKUPS_PATH, STAFF_PATH } from '../app/navigation'
import { PageLink } from '../app/PageLink'
import list from '../callers/Callers.module.css'
import { ago, describeProblem } from '../callers/words'
import { modelCost, money } from '../session/presentation'
import { useNow } from '../session/useNow'
import staff from '../staff/Staff.module.css'
import styles from './Conversations.module.css'
import { dayLine, factLines, FILTERS, noteMeta, outcomeAsked, outcomeLabel, placeLabel, shown, whyNotTheModel, type Filter } from './words'

// A new note arrives a few seconds after each session ends.
const REFRESH_MS = 10_000

/**
 * Conversations: a note on each session, written a few seconds after it
 * ends. The outcome and the order are the backend's; the words are the
 * model's when they matched the conversation, and fixed wording otherwise.
 */
export function ConversationsPage() {
  const now = useNow()
  const [filter, setFilter] = useState<Filter>('all')
  const [open, setOpen] = useState<string | null>(null)
  const outcome = outcomeAsked(filter)
  const summaries = useQuery({
    queryKey: ['summaries', outcome ?? 'all'],
    queryFn: () => api.summaries(outcome),
    refetchInterval: REFRESH_MS,
    retry: false,
  })
  const rows = shown(summaries.data ?? [], filter)

  return (
    <div className={staff.screen}>
      <header className={staff.topBar}>
        <div className={staff.titleGroup}>
          <HomeLink />
          <h1 className={staff.title}>
            Staff <span className={staff.titleSub}>Conversations</span>
          </h1>
        </div>
        <nav className={staff.pageLinks} aria-label="Pages">
          <PageLink to={STAFF_PATH} className={staff.button}>
            Handoffs
          </PageLink>
          <PageLink to={PICKUPS_PATH} className={staff.button}>
            Pickups
          </PageLink>
          <PageLink to={CALLERS_PATH} className={staff.button}>
            Callers
          </PageLink>
          <PageLink to={LIVE_PATH} className={staff.button}>
            Live session
          </PageLink>
        </nav>
      </header>

      {summaries.isError && (
        <p role="alert" className={staff.banner}>
          Cannot reach the backend. Start it with `make dev`, then reload.
        </p>
      )}

      <main className={list.main}>
        <div className={list.intro}>
          <h2 className={list.heading}>What happened in each conversation</h2>
          <p className={list.lede}>
            A note on every session, written a few seconds after it ends. How it ended and what was ordered come from the backend.
            The words are the model’s when everything they mention was really said, and written from the log when not.
          </p>
        </div>

        <DayCost />

        <div className={styles.filters} role="group" aria-label="Show">
          {FILTERS.map((option) => (
            <button
              key={option.filter}
              type="button"
              className={styles.filter}
              aria-pressed={filter === option.filter}
              onClick={() => {
                setFilter(option.filter)
                setOpen(null)
              }}
            >
              {option.label}
            </button>
          ))}
        </div>

        <section className={list.panel} aria-labelledby="conversations-title" aria-busy={summaries.isLoading}>
          <h3 id="conversations-title" className={list.panelTitle}>
            Latest first
          </h3>
          {summaries.isLoading ? (
            <p className={list.empty}>Loading…</p>
          ) : rows.length === 0 ? (
            <p className={list.empty}>
              {filter === 'all' ? 'No notes yet. One appears a few seconds after a session ends.' : 'Nothing here.'}
            </p>
          ) : (
            <ul className={list.list} aria-label="Conversations">
              {rows.map((summary) => (
                <Row
                  key={summary.session_id}
                  summary={summary}
                  nowMs={now}
                  open={open === summary.session_id}
                  onToggle={() => setOpen(open === summary.session_id ? null : summary.session_id)}
                />
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  )
}

/** What the model has cost today, if there is a price list to work it out. */
function DayCost() {
  const day = useQuery({ queryKey: ['costs-today'], queryFn: api.costsToday, refetchInterval: REFRESH_MS, retry: false })
  if (!day.data) return null
  return (
    <section className={styles.day} aria-label="Model cost today">
      <p className={styles.dayLine}>
        <strong>Today</strong> {dayLine(day.data)}
      </p>
      <p className={list.rowMeta}>
        Prices checked {day.data.prices_checked_on}, from{' '}
        <a href={day.data.prices_source} target="_blank" rel="noreferrer">
          the provider’s price list
        </a>
        .
      </p>
    </section>
  )
}

interface RowProps {
  summary: ConversationSummary
  nowMs: number
  open: boolean
  onToggle: () => void
}

function Row({ summary, nowMs, open, onToggle }: RowProps) {
  const detailsId = `details-${summary.session_id}`
  return (
    <li className={list.row} aria-label={`${outcomeLabel(summary.outcome)}, ${placeLabel(summary)}`}>
      <div className={list.rowMain}>
        <span className={`${list.chip} ${styles.outcome}`} data-outcome={summary.outcome}>
          {outcomeLabel(summary.outcome)}
        </span>
        <span className={list.rowTitle}>{placeLabel(summary)}</span>
        {summary.order_number !== null && summary.total_cents !== null && (
          <span className={list.number}>
            #{summary.order_number} · {money(summary.total_cents)}
          </span>
        )}
        <span className={styles.when}>ended {ago(summary.ended_at, nowMs)}</span>
      </div>
      <p className={styles.gist}>{summary.gist}</p>
      {summary.follow_up && (
        <p className={styles.followUp}>
          <strong>Follow up:</strong> {summary.follow_up}
        </p>
      )}
      <div className={styles.rowFoot}>
        <span className={list.rowMeta}>{noteMeta(summary)}</span>
        <button type="button" className={styles.toggle} aria-expanded={open} aria-controls={detailsId} onClick={onToggle}>
          {open ? 'Hide details' : 'Details'}
        </button>
      </div>
      {open && <Details id={detailsId} sessionId={summary.session_id} />}
    </li>
  )
}

function Details({ id, sessionId }: { id: string; sessionId: string }) {
  const queryClient = useQueryClient()
  const detail = useQuery({ queryKey: ['summary', sessionId], queryFn: () => api.summary(sessionId), retry: false })
  const rewriting = useMutation({
    mutationFn: () => api.rewriteSummary(sessionId),
    onSuccess: (written) => {
      queryClient.setQueryData(['summary', sessionId], written)
      return queryClient.invalidateQueries({ queryKey: ['summaries'] })
    },
  })
  const problem = describeProblem(rewriting.error)

  if (detail.isLoading) {
    return (
      <div id={id} className={styles.details}>
        <p className={list.empty}>Loading…</p>
      </div>
    )
  }
  if (!detail.data) {
    return (
      <div id={id} className={styles.details}>
        <p className={list.empty}>The details could not be loaded.</p>
      </div>
    )
  }
  const { facts } = detail.data
  const whyNot = whyNotTheModel(detail.data.rejected_reason)
  return (
    <div id={id} className={styles.details}>
      {facts.order && (
        <p className={styles.order}>
          {facts.order.lines.map((line) => `${line.quantity} × ${line.item_name}`).join(', ')}
        </p>
      )}
      <dl className={styles.facts}>
        {factLines(facts).map((fact) => (
          <div key={fact.label} className={styles.fact}>
            <dt>{fact.label}</dt>
            <dd>{fact.value}</dd>
          </div>
        ))}
      </dl>
      {detail.data.summary_cost && <p className={list.rowMeta}>Writing this note cost {modelCost(detail.data.summary_cost)}.</p>}
      {whyNot && <p className={list.rowMeta}>{whyNot}</p>}
      {problem && (
        <p role="alert" className={list.problem}>
          {problem}
        </p>
      )}
      <div className={list.actions}>
        <button type="button" className={staff.button} disabled={rewriting.isPending} onClick={() => rewriting.mutate()}>
          {rewriting.isPending ? 'Writing…' : 'Write it again'}
        </button>
      </div>
    </div>
  )
}
