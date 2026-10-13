import { useState } from 'react'

import type { HandoffPackage, HandoffStep } from '../api/client'
import { money } from '../session/presentation'
import { summarise } from '../session/timeline'
import { DiscountRows } from '../views/shared/DiscountRows'
import styles from './Staff.module.css'
import type { StaffDesk } from './useStaffDesk'
import { holdLabel, outcomeSentence, statusLabel, waitingFor } from './words'

/** One handoff in full: why, what to do, and everything needed to carry on. */
export function PackageView({ desk, handoff }: { desk: StaffDesk; handoff: HandoffPackage }) {
  const resolved = handoff.status === 'resolved'

  return (
    <article aria-label="Handoff package" className={styles.package}>
      <header className={styles.packageHead}>
        <h2 className={styles.packageTitle}>{handoff.reason.title}</h2>
        <span className={styles.entryStatus} data-status={handoff.status}>
          {statusLabel(handoff)}
        </span>
        <p className={styles.packageMeta}>
          {handoff.lane} · {handoff.language.toUpperCase()}
          {!resolved && ` · waiting ${waitingFor(handoff, desk.now)}`}
        </p>
      </header>

      <section aria-label="What to do" className={styles.todo}>
        <h3 className={styles.sectionTitle}>What to do</h3>
        <p>{handoff.outstanding}</p>
      </section>

      {resolved ? <Outcome handoff={handoff} /> : <Actions key={handoff.handoff_id} desk={desk} handoff={handoff} />}

      <div className={styles.columns}>
        <Cart handoff={handoff} />
        <Conversation handoff={handoff} />
      </div>
      <Steps handoff={handoff} />
    </article>
  )
}

function Outcome({ handoff }: { handoff: HandoffPackage }) {
  return (
    <section aria-label="Outcome" className={styles.outcome} data-outcome={handoff.outcome}>
      <p>
        {outcomeSentence(handoff)}
        {handoff.order && ` Total ${money(handoff.order.total_cents)}.`}
      </p>
      {handoff.note && <p className={styles.note}>Note: {handoff.note}</p>}
    </section>
  )
}

/** The two things a person can do: say they have it, and close it. */
function Actions({ desk, handoff }: { desk: StaffDesk; handoff: HandoffPackage }) {
  const [note, setNote] = useState('')
  const nothingToPlace = handoff.cart.lines.length === 0

  return (
    <section aria-label="Actions" className={styles.actions}>
      {handoff.status === 'open' && (
        <div className={styles.claim}>
          <label className={styles.field}>
            <span>Your name</span>
            <input value={desk.staffName} placeholder="staff" maxLength={80} onChange={(event) => desk.setStaffName(event.target.value)} />
          </label>
          <button type="button" className={styles.button} disabled={desk.busy} onClick={desk.acknowledge}>
            I've got this one
          </button>
        </div>
      )}

      <label className={styles.field}>
        <span>Note (optional)</span>
        <input value={note} maxLength={500} placeholder="What happened, for the record" onChange={(event) => setNote(event.target.value)} />
      </label>
      <div className={styles.resolve}>
        <button
          type="button"
          className={`${styles.button} ${styles.buttonPrimary}`}
          disabled={desk.busy || nothingToPlace}
          onClick={() => desk.resolve('order_placed', note)}
        >
          Place order as it stands · {money(handoff.total_cents)}
        </button>
        <button type="button" className={styles.button} disabled={desk.busy} onClick={() => desk.resolve('cancelled', note)}>
          Cancel order
        </button>
      </div>
      {nothingToPlace && <p className={styles.hint}>The cart is empty, so there is no order to place.</p>}
      {desk.problem && (
        <p role="alert" className={styles.problem}>
          {desk.problem}
        </p>
      )}
    </section>
  )
}

function Cart({ handoff }: { handoff: HandoffPackage }) {
  const { cart, stock } = handoff
  return (
    <section aria-label="Cart" className={styles.panel}>
      <h3 className={styles.sectionTitle}>Cart</h3>
      {cart.lines.length === 0 && <p className={styles.hint}>Empty.</p>}
      <ul className={styles.lines}>
        {cart.lines.map((line) => (
          <li key={line.line_no} className={styles.line}>
            <span className={styles.lineNumber}>L{line.line_no}</span>
            <span>
              {line.quantity} × {line.item_name}
              {line.options.length > 0 && <span className={styles.lineOptions}>{line.options.map((option) => option.option_name).join(' · ')}</span>}
            </span>
            <span className={styles.amount}>{money(line.line_total_cents)}</span>
          </li>
        ))}
      </ul>
      <dl className={styles.totals}>
        <dt>Subtotal</dt>
        <dd>{money(cart.subtotal_cents)}</dd>
        <DiscountRows discounts={cart.discounts} className={styles.saving} />
        <dt>Tax</dt>
        <dd>{money(cart.tax_cents)}</dd>
        <dt className={styles.totalLabel}>Total</dt>
        <dd className={styles.totalValue}>{money(cart.total_cents)}</dd>
      </dl>

      {stock.length > 0 && (
        <>
          <h3 className={styles.sectionTitle}>Stock</h3>
          <ul className={styles.stock}>
            {stock.map((held) => (
              <li key={`${held.item_code}:${held.status}`} data-held={held.held}>
                <span>
                  {held.quantity} × {held.item_name}
                </span>
                <span>{holdLabel(held)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

function Conversation({ handoff }: { handoff: HandoffPackage }) {
  return (
    <section aria-label="Conversation" className={styles.panel}>
      <h3 className={styles.sectionTitle}>Conversation</h3>
      {handoff.earlier_lines > 0 && <p className={styles.hint}>{handoff.earlier_lines} earlier lines not shown.</p>}
      {handoff.transcript.length === 0 && <p className={styles.hint}>Nothing was said.</p>}
      <ol className={styles.transcript}>
        {handoff.transcript.map((line) => (
          <li key={line.at + line.text} data-speaker={line.speaker}>
            <span className={styles.speaker}>{line.speaker}</span>
            <span>{line.text}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}

function Steps({ handoff }: { handoff: HandoffPackage }) {
  return (
    <section aria-label="What the backend decided" className={styles.panel}>
      <h3 className={styles.sectionTitle}>What the backend decided</h3>
      {handoff.earlier_steps > 0 && <p className={styles.hint}>{handoff.earlier_steps} earlier steps not shown.</p>}
      {handoff.steps.length === 0 && <p className={styles.hint}>The agent used no tools.</p>}
      <ol className={styles.steps}>
        {handoff.steps.map((step) => (
          <li key={step.at + step.tool} className={styles.step}>
            <span className={styles.stepTool}>{step.tool}</span>
            <span className={styles.stepDetail}>{stepDetail(step)}</span>
            <span className={styles.stepDecision} data-decision={step.decision ?? 'none'}>
              {step.decision ?? (step.tool === 'timer' ? 'clock' : 'read')}
            </span>
          </li>
        ))}
      </ol>
    </section>
  )
}

/** A step's arguments and the rule behind its decision, on one line. */
function stepDetail(step: HandoffStep): string {
  const asked = step.tool === 'timer' ? (step.message ?? '') : summarise(step.arguments)
  return [asked, step.rule?.replaceAll('_', ' ')].filter(Boolean).join(' · ') || '—'
}
