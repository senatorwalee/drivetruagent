import { useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'

import { Field } from '../menu-admin/Field'
import menuStyles from '../menu-admin/MenuAdmin.module.css'
import { QuantityField } from '../menu-admin/QuantityField'
import { Segmented } from '../menu-admin/Segmented'
import { SizesField } from '../menu-admin/SizesField'
import { emptyForm, firstSizes } from '../menu-admin/words'
import type { FormProblems, ItemForm, SizeRow } from '../menu-admin/words'
import staff from '../staff/Staff.module.css'
import styles from './Onboarding.module.css'
import { checkItem, draftItemFrom, formFromDraftItem, itemsBySection, newId, priceLabel, quantityLabel, sectionKey, sectionNames } from './words'
import type { Draft, DraftItem, MenuToday } from './words'

interface ItemsStepProps {
  draft: Draft
  today: MenuToday
  update: (change: Partial<Draft>) => void
}

const STARTS = [
  { value: 'add', label: 'Add to it' },
  { value: 'fresh', label: 'Start fresh' },
] as const

/**
 * Entering the menu. The form stays put and the list grows beside it, so
 * adding the twentieth item takes as little as adding the first.
 */
export function ItemsStep({ draft, today, update }: ItemsStepProps) {
  const sections = sectionNames(draft, today)
  const [form, setForm] = useState<ItemForm>(() => emptyForm(draft.items.at(-1)?.section ?? sections[0]))
  const [editing, setEditing] = useState<string | null>(null)
  const [problems, setProblems] = useState<FormProblems>({})
  const [added, setAdded] = useState<string | null>(null)
  const nameInput = useRef<HTMLInputElement>(null)
  const groups = itemsBySection(draft, today)

  const edit = (field: Exclude<keyof ItemForm, 'sizes'>) => (value: string) => {
    setForm((current) => ({ ...current, [field]: value }))
    setProblems((current) => ({ ...current, [field]: undefined }))
    setAdded(null)
  }

  function editSizes(sizes: SizeRow[] | null, price?: string) {
    setForm((current) => ({ ...current, sizes, price: price ?? current.price }))
    setProblems((current) => ({ ...current, sizes: undefined, price: undefined }))
    setAdded(null)
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    const found = checkItem(form, draft, today, editing)
    if (Object.keys(found).length > 0) {
      setProblems(found)
      return
    }
    const before = draft.items.find((item) => item.id === editing)
    const item = draftItemFrom(form, editing ?? newId(), before)
    update({ items: before ? draft.items.map((each) => (each.id === editing ? item : each)) : [...draft.items, item] })
    setAdded(`${before ? 'Saved' : 'Added'} ${item.name_en}.`)
    startNext(item.section)
  }

  /** Ready for the next item, in the same section with the same quantity. */
  function startNext(section: string) {
    setForm((current) => ({ ...emptyForm(section), stockMode: editing ? 'unlimited' : current.stockMode }))
    setEditing(null)
    setProblems({})
    nameInput.current?.focus()
  }

  function open(item: DraftItem) {
    setForm(formFromDraftItem(item))
    setEditing(item.id)
    setProblems({})
    setAdded(null)
    nameInput.current?.focus()
  }

  function remove(item: DraftItem) {
    update({ items: draft.items.filter((each) => each.id !== item.id) })
    if (editing === item.id) startNext(item.section)
    setAdded(`Removed ${item.name_en}.`)
  }

  function addSection(name: string) {
    update({ sections: [...draft.sections, name] })
    edit('categoryCode')(name)
  }

  return (
    <>
      {today.items.length > 0 && (
        <div className={styles.startChoice}>
          <p>
            Your menu has <strong>{today.items.length} items</strong> today.
          </p>
          <Segmented
            label="What to do with them"
            options={[...STARTS]}
            value={draft.replaceMenu ? 'fresh' : 'add'}
            onChange={(choice) => {
              update({ replaceMenu: choice === 'fresh' })
              // A name that clashed with the menu may not any more.
              setProblems({})
            }}
          />
          <p className={menuStyles.help}>{draft.replaceMenu ? 'They come off the menu when you go live, not before.' : 'They stay on the menu, and these join them.'}</p>
        </div>
      )}

      <div className={styles.entry}>
        <form className={styles.composer} onSubmit={submit} noValidate aria-label={editing ? 'Edit item' : 'Add an item'}>
          <SectionPicker sections={sections} chosen={form.categoryCode} onChoose={edit('categoryCode')} onAdd={addSection} />

          <div className={styles.nameAndPrice}>
            <Field label="Item name" problem={problems.nameEn}>
              {(props) => (
                <input {...props} ref={nameInput} value={form.nameEn} onChange={(e) => edit('nameEn')(e.target.value)} maxLength={60} autoComplete="off" required autoFocus />
              )}
            </Field>
            {!form.sizes && (
              <Field label="Price" problem={problems.price}>
                {(props) => (
                  <span className={menuStyles.prefixed}>
                    <span aria-hidden="true">$</span>
                    <input {...props} inputMode="decimal" placeholder="0.00" value={form.price} onChange={(e) => edit('price')(e.target.value)} required />
                  </span>
                )}
              </Field>
            )}
          </div>

          {form.sizes ? (
            <SizesField sizes={form.sizes} problem={problems.sizes} onChange={editSizes} />
          ) : (
            <button type="button" className={menuStyles.addSizes} onClick={() => editSizes(firstSizes(form.price))}>
              Add sizes
            </button>
          )}

          <QuantityField mode={form.stockMode} stock={form.stock} problem={problems.stock} onMode={edit('stockMode')} onStock={edit('stock')} />

          <details key={editing ?? 'new'} className={styles.more} open={Boolean(form.descriptionEn || form.descriptionFr || form.nameFr)}>
            <summary>Description and French name</summary>
            <div className={styles.moreFields}>
              <Field label="Description" hint="The agent can read this to guests." problem={problems.descriptionEn}>
                {(props) => <textarea {...props} rows={2} value={form.descriptionEn} onChange={(e) => edit('descriptionEn')(e.target.value)} maxLength={240} />}
              </Field>
              <Field label="Name in French" hint="If empty, the English name is used.">
                {(props) => <input {...props} lang="fr" value={form.nameFr} onChange={(e) => edit('nameFr')(e.target.value)} maxLength={60} />}
              </Field>
              <Field label="Description in French">
                {(props) => <textarea {...props} lang="fr" rows={2} value={form.descriptionFr} onChange={(e) => edit('descriptionFr')(e.target.value)} maxLength={240} />}
              </Field>
            </div>
          </details>

          <div className={menuStyles.saveActions}>
            <button type="submit" className={`${staff.button} ${staff.buttonPrimary}`}>
              {editing ? 'Save item' : 'Add item'}
            </button>
            {editing && (
              <button type="button" className={staff.button} onClick={() => startNext(form.categoryCode)}>
                Cancel
              </button>
            )}
          </div>
          <p role="status" className={styles.added}>
            {added}
          </p>
        </form>

        <section className={styles.list} aria-label="Items entered">
          {groups.length === 0 ? (
            <p className={styles.emptyList}>Your items will show up here as you add them.</p>
          ) : (
            groups.map((group) => (
              <section key={sectionKey(group.section)} aria-label={group.section}>
                <h3 className={menuStyles.groupTitle}>
                  {group.section} <span className={menuStyles.groupCount}>{group.items.length}</span>
                </h3>
                <ul className={styles.rows}>
                  {group.items.map((item) => (
                    <li key={item.id} className={styles.itemRow} aria-current={item.id === editing ? 'true' : undefined}>
                      <button type="button" className={styles.itemOpen} aria-label={`Edit ${item.name_en}`} onClick={() => open(item)}>
                        <span className={styles.itemName}>{item.name_en}</span>
                        <span className={styles.itemQuantity}>{quantityLabel(item)}</span>
                        <span className={styles.itemPrice}>{priceLabel(item)}</span>
                      </button>
                      <button type="button" className={styles.itemRemove} aria-label={`Remove ${item.name_en}`} onClick={() => remove(item)}>
                        <span aria-hidden="true">×</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))
          )}
        </section>
      </div>
    </>
  )
}

interface SectionPickerProps {
  sections: string[]
  chosen: string
  onChoose: (section: string) => void
  onAdd: (section: string) => void
}

/** Which part of the menu an item goes in, or a new part. */
function SectionPicker({ sections, chosen, onChoose, onAdd }: SectionPickerProps) {
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')

  function add() {
    const trimmed = name.trim()
    if (trimmed) {
      const known = sections.find((each) => sectionKey(each) === sectionKey(trimmed))
      if (known) onChoose(known)
      else onAdd(trimmed)
    }
    setName('')
    setAdding(false)
  }

  function onKey(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      setName('')
      setAdding(false)
    }
    if (event.key !== 'Enter') return
    // Enter here finishes the section, not the item.
    event.preventDefault()
    add()
  }

  return (
    <div className={styles.sections}>
      <span className={menuStyles.label}>Section</span>
      <div className={menuStyles.chips} role="group" aria-label="Section">
        {sections.map((section) => (
          <button key={section} type="button" className={menuStyles.chip} aria-pressed={sectionKey(section) === sectionKey(chosen)} onClick={() => onChoose(section)}>
            {section}
          </button>
        ))}
        {adding ? (
          <input
            className={styles.newSection}
            aria-label="New section name"
            placeholder="Section name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={onKey}
            onBlur={add}
            maxLength={40}
            autoFocus
          />
        ) : (
          <button type="button" className={styles.addChip} aria-label="New section" onClick={() => setAdding(true)}>
            + New
          </button>
        )}
      </div>
    </div>
  )
}
