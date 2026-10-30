import { useState } from 'react'
import type { FormEvent } from 'react'

import { Field } from '../menu-admin/Field'
import menuStyles from '../menu-admin/MenuAdmin.module.css'
import { money } from '../session/presentation'
import staff from '../staff/Staff.module.css'
import styles from './Onboarding.module.css'
import { checkCombo, cheapestSet, comboParts, newId, parseSaving, sectionKey, sectionNames } from './words'
import type { ComboPart, Draft, DraftCombo, MenuToday } from './words'

interface CombosStepProps {
  draft: Draft
  today: MenuToday
  update: (change: Partial<Draft>) => void
}

/** The combos entered, and a form for one more. */
export function CombosStep({ draft, today, update }: CombosStepProps) {
  // null: the form is closed. 'new' or a combo's id: it is open for that.
  const [open, setOpen] = useState<string | null>(null)

  function save(combo: DraftCombo) {
    const known = draft.combos.some((each) => each.id === combo.id)
    update({ combos: known ? draft.combos.map((each) => (each.id === combo.id ? combo : each)) : [...draft.combos, combo] })
    setOpen(null)
  }

  if (draft.items.length === 0) return <p className={styles.emptyList}>Add your menu first, then come back to this.</p>

  const editing = draft.combos.find((combo) => combo.id === open)

  return (
    <div className={styles.combos}>
      {draft.combos.length > 0 && (
        <ul className={styles.comboList}>
          {draft.combos.map((combo) => (
            <ComboCard
              key={combo.id}
              combo={combo}
              parts={comboParts(combo.slots, draft, today)}
              onEdit={() => setOpen(combo.id)}
              onRemove={() => update({ combos: draft.combos.filter((each) => each.id !== combo.id) })}
            />
          ))}
        </ul>
      )}

      {open ? (
        <ComboForm key={open} combo={editing} draft={draft} today={today} onSave={save} onCancel={() => setOpen(null)} />
      ) : (
        <button type="button" className={`${staff.button} ${draft.combos.length === 0 ? staff.buttonPrimary : ''}`} onClick={() => setOpen('new')}>
          {draft.combos.length === 0 ? 'Add a combo' : 'Add another combo'}
        </button>
      )}
    </div>
  )
}

interface ComboCardProps {
  combo: DraftCombo
  parts: ComboPart[]
  onEdit: () => void
  onRemove: () => void
}

function ComboCard({ combo, parts, onEdit, onRemove }: ComboCardProps) {
  const total = cheapestSet(parts)
  return (
    <li className={styles.comboCard}>
      <div>
        <p className={styles.comboName}>{combo.name}</p>
        <p className={styles.comboParts}>{combo.slots.join(' + ')}</p>
      </div>
      <p className={styles.comboPrice}>
        <span>Saves {money(combo.savingCents)}</span>
        <span className={styles.comboFrom}>from {money(Math.max(total - combo.savingCents, 0))}</span>
      </p>
      <div className={styles.comboActions}>
        <button type="button" className={styles.quiet} aria-label={`Edit ${combo.name}`} onClick={onEdit}>
          Edit
        </button>
        <button type="button" className={styles.quiet} aria-label={`Remove ${combo.name}`} onClick={onRemove}>
          Remove
        </button>
      </div>
    </li>
  )
}

interface ComboFormProps {
  combo: DraftCombo | undefined
  draft: Draft
  today: MenuToday
  onSave: (combo: DraftCombo) => void
  onCancel: () => void
}

function ComboForm({ combo, draft, today, onSave, onCancel }: ComboFormProps) {
  const [name, setName] = useState(combo?.name ?? '')
  const [slots, setSlots] = useState<string[]>(combo?.slots ?? [])
  const [saving, setSaving] = useState(combo ? (combo.savingCents / 100).toFixed(2) : '')
  const [problem, setProblem] = useState<string | null>(null)

  const parts = comboParts(slots, draft, today)
  const savingCents = parseSaving(saving)
  const total = cheapestSet(parts)
  const worksOut = slots.length >= 2 && savingCents !== null && savingCents > 0 && savingCents < total
  // Only sections something can be picked from.
  const choosable = sectionNames(draft, today).filter((section) => comboParts([section], draft, today)[0].choices > 0)

  function submit(event: FormEvent) {
    event.preventDefault()
    const found = checkCombo({ name, savingCents, slots }, combo?.id ?? null, draft, today)
    if (found) {
      setProblem(found)
      return
    }
    onSave({ id: combo?.id ?? newId(), name: name.trim(), savingCents: savingCents ?? 0, slots })
  }

  return (
    <form className={styles.comboForm} onSubmit={submit} noValidate aria-label={combo ? `Edit ${combo.name}` : 'New combo'}>
      <Field label="Combo name">
        {(props) => (
          <input
            {...props}
            placeholder="Burger combo"
            value={name}
            onChange={(event) => {
              setName(event.target.value)
              setProblem(null)
            }}
            maxLength={60}
            autoComplete="off"
            autoFocus
          />
        )}
      </Field>

      <div className={styles.comboBuild}>
        <span className={menuStyles.label}>What’s in it</span>
        <p className={menuStyles.help}>Pick a section for each part. The guest chooses any item from it. Start with the main item: the agent offers the combo once it’s ordered.</p>
        {parts.length > 0 && (
          <ol className={styles.partList}>
            {parts.map((part, index) => (
              <li key={`${part.section}-${index}`} className={styles.part}>
                <span className={styles.partName}>
                  {index === 0 && <span className={styles.mainTag}>Main</span>}
                  {part.section}
                </span>
                <span className={styles.partDetail}>
                  {part.choices === 1 ? `${part.cheapest?.name}` : `Any of ${part.choices}, from ${money(part.cheapest?.cents ?? 0)}`}
                </span>
                <button
                  type="button"
                  className={styles.itemRemove}
                  aria-label={`Remove ${part.section} from the combo`}
                  onClick={() => {
                    setSlots(slots.filter((_, position) => position !== index))
                    setProblem(null)
                  }}
                >
                  <span aria-hidden="true">×</span>
                </button>
              </li>
            ))}
          </ol>
        )}
        <div className={menuStyles.chips} role="group" aria-label="Add a part">
          {choosable.map((section) => (
            <button
              key={sectionKey(section)}
              type="button"
              className={styles.addChip}
              onClick={() => {
                setSlots([...slots, section])
                setProblem(null)
              }}
            >
              + {section}
            </button>
          ))}
        </div>
      </div>

      <Field label="The guest saves">
        {(props) => (
          <span className={`${menuStyles.prefixed} ${styles.savingInput}`}>
            <span aria-hidden="true">$</span>
            <input
              {...props}
              inputMode="decimal"
              placeholder="2.00"
              value={saving}
              onChange={(event) => {
                setSaving(event.target.value)
                setProblem(null)
              }}
            />
          </span>
        )}
      </Field>

      {worksOut && (
        <p className={styles.preview} role="status">
          The cheapest set costs {money(total)}. With the combo, the guest pays <strong>{money(total - savingCents)}</strong>.
        </p>
      )}
      {problem && (
        <p role="alert" className={menuStyles.fieldProblem}>
          {problem}
        </p>
      )}

      <div className={menuStyles.saveActions}>
        <button type="submit" className={`${staff.button} ${staff.buttonPrimary}`}>
          {combo ? 'Save combo' : 'Add combo'}
        </button>
        <button type="button" className={staff.button} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}
