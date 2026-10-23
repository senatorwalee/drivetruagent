import { useEffect, useId, useRef, useState } from 'react'
import type { DragEvent, FormEvent, ReactNode } from 'react'

import { api } from '../api/client'
import type { AdminCategory, AdminItem } from '../api/client'
import staff from '../staff/Staff.module.css'
import { ItemPicture } from './ItemPicture'
import styles from './MenuAdmin.module.css'
import { ACCEPT, PhotoProblem, preparePhoto } from './photo'
import {
  changesFrom,
  checkForm,
  emptyForm,
  explainError,
  formFromItem,
  newItemFrom,
  optionsLabel,
  sameForm,
} from './words'
import type { FormProblems, ItemForm } from './words'

type PhotoChange = { kind: 'keep' } | { kind: 'replace'; blob: Blob; preview: string } | { kind: 'remove' }

interface ItemEditorProps {
  /** The item being edited, or null to add a new one. */
  item: AdminItem | null
  categories: AdminCategory[]
  /** For a new item: the category it starts in. */
  defaultCategory: string
  /** Shown at the top until the next change: "Saved." */
  notice: string | null
  /** Called with the item as the backend now has it. */
  onSaved: (item: AdminItem, notice: string) => void
  onClose: () => void
  onDirtyChange: (dirty: boolean) => void
}

/**
 * Adding or editing one item. Nothing is sent until Save, photo included, so
 * Cancel always leaves the item as it was.
 */
export function ItemEditor({ item, categories, defaultCategory, notice, onSaved, onClose, onDirtyChange }: ItemEditorProps) {
  const initial = item ? formFromItem(item) : emptyForm(defaultCategory)
  const [form, setForm] = useState<ItemForm>(initial)
  const [photo, setPhoto] = useState<PhotoChange>({ kind: 'keep' })
  const [problems, setProblems] = useState<FormProblems & { photo?: string; form?: string }>({})
  const [busy, setBusy] = useState(false)
  const [confirmingRemove, setConfirmingRemove] = useState(false)
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const noticeRef = useRef<HTMLParagraphElement>(null)
  const ids = useId()

  // Save is at the bottom of a long form; bring "Saved." into view.
  useEffect(() => {
    if (notice) noticeRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }, [notice])

  const dirty = !sameForm(form, initial) || photo.kind !== 'keep'
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange])

  // A preview's object URL holds the image in memory until it is let go.
  useEffect(() => {
    if (photo.kind !== 'replace') return
    return () => URL.revokeObjectURL(photo.preview)
  }, [photo])

  const name = form.nameEn.trim() || 'New item'
  const savedPhoto = item?.photo ? api.photoUrl(item.item_code, item.photo.version) : null
  const pictureSrc = photo.kind === 'replace' ? photo.preview : photo.kind === 'remove' ? null : savedPhoto

  const edit = (field: keyof ItemForm) => (value: string) => {
    setForm((current) => ({ ...current, [field]: value }))
    setProblems((current) => ({ ...current, [field]: undefined, form: undefined }))
  }

  async function choosePhoto(file: File | undefined) {
    if (!file) return
    setProblems((current) => ({ ...current, photo: undefined }))
    try {
      const blob = await preparePhoto(file)
      setPhoto({ kind: 'replace', blob, preview: URL.createObjectURL(blob) })
    } catch (error) {
      setProblems((current) => ({
        ...current,
        photo: error instanceof PhotoProblem ? error.message : 'This photo could not be read.',
      }))
    }
  }

  function dropPhoto(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    void choosePhoto(event.dataTransfer.files[0])
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    const found = checkForm(form)
    if (Object.keys(found).length > 0) {
      setProblems(found)
      return
    }
    setBusy(true)
    let saved = item
    try {
      if (saved === null) {
        saved = await api.createItem(newItemFrom(form))
      } else {
        const changes = changesFrom(form, saved)
        if (Object.keys(changes).length > 0) saved = await api.updateItem(saved.item_code, changes)
      }
    } catch (error) {
      setBusy(false)
      const { field, message } = explainError(error, form)
      setProblems(field ? { [field]: message } : { form: message })
      return
    }
    try {
      if (photo.kind === 'replace') saved = await api.setPhoto(saved.item_code, photo.blob)
      if (photo.kind === 'remove' && saved.photo) saved = await api.removePhoto(saved.item_code)
    } catch (error) {
      // The item itself was saved; only the photo is left to do.
      setBusy(false)
      onSaved(saved, item ? 'Saved, except the photo.' : 'Added, except the photo.')
      setProblems({ photo: explainError(error, form).message })
      return
    }
    setBusy(false)
    setPhoto({ kind: 'keep' })
    // As the backend has it: "5.5" typed comes back as "5.50".
    setForm(formFromItem(saved))
    onSaved(saved, item ? 'Saved. Guests and the agent see it from the next order.' : 'Added to the menu.')
  }

  async function setActive(isActive: boolean) {
    if (!item) return
    setBusy(true)
    try {
      const saved = await api.updateItem(item.item_code, { is_active: isActive })
      setConfirmingRemove(false)
      onSaved(saved, isActive ? 'Back on the menu.' : 'Taken off the menu. It can be put back here.')
    } catch (error) {
      setProblems({ form: explainError(error, form).message })
    } finally {
      setBusy(false)
    }
  }

  const titleId = `${ids}-title`

  return (
    <aside className={styles.editor} aria-labelledby={titleId}>
      <form className={styles.editorForm} onSubmit={save} noValidate>
        <div className={styles.editorHead}>
          <div>
            <p className={styles.editorKicker}>{item ? 'Edit item' : 'New item'}</p>
            <h2 id={titleId} className={styles.editorTitle}>
              {name}
            </h2>
          </div>
          <button type="button" className={staff.button} onClick={onClose}>
            Close
          </button>
        </div>

        {notice && (
          <p ref={noticeRef} role="status" className={styles.notice}>
            {notice}
          </p>
        )}
        {problems.form && (
          <p role="alert" className={staff.problem}>
            {problems.form}
          </p>
        )}
        {item && !item.is_active && (
          <div className={styles.removedBanner}>
            <p>
              <strong>Not on the menu.</strong> Guests and the agent don't see it.
            </p>
            <button type="button" className={`${staff.button} ${staff.buttonPrimary}`} disabled={busy} onClick={() => void setActive(true)}>
              Put back on the menu
            </button>
          </div>
        )}

        <section aria-label="Photo" className={styles.photoSection}>
          <div
            className={styles.dropZone}
            data-dragging={dragging}
            onDragOver={(event) => {
              event.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={dropPhoto}
          >
            <ItemPicture src={pictureSrc} categoryCode={form.categoryCode} label={name} size="large" />
          </div>
          <div className={styles.photoActions}>
            <button type="button" className={staff.button} onClick={() => fileInput.current?.click()}>
              {pictureSrc ? 'Replace photo' : 'Upload photo'}
            </button>
            {pictureSrc && (
              <button type="button" className={staff.button} onClick={() => setPhoto({ kind: 'remove' })}>
                Remove photo
              </button>
            )}
            {photo.kind !== 'keep' && item && (
              <button type="button" className={styles.linkButton} onClick={() => setPhoto({ kind: 'keep' })}>
                Undo
              </button>
            )}
            <input
              ref={fileInput}
              className={styles.visuallyHidden}
              type="file"
              accept={ACCEPT}
              aria-label="Photo file"
              tabIndex={-1}
              onChange={(event) => {
                void choosePhoto(event.target.files?.[0])
                event.target.value = ''
              }}
            />
          </div>
          <p className={styles.help}>
            {photo.kind === 'remove'
              ? 'The photo will be removed when you save. A placeholder shows instead.'
              : 'Drop a photo here or upload one. JPEG, PNG or WebP; large photos are shrunk for you.'}
            {photo.kind === 'keep' && item?.photo?.credit ? ` Photo: ${item.photo.credit}.` : ''}
          </p>
          {problems.photo && (
            <p role="alert" className={styles.fieldProblem}>
              {problems.photo}
            </p>
          )}
        </section>

        <div className={styles.fieldRow}>
          <Field label="Name" hint="English" problem={problems.nameEn}>
            {(props) => <input {...props} value={form.nameEn} onChange={(e) => edit('nameEn')(e.target.value)} maxLength={60} required />}
          </Field>
          <Field label="Name in French" hint="Optional; the English name is used if empty" problem={problems.nameFr}>
            {(props) => <input {...props} lang="fr" value={form.nameFr} onChange={(e) => edit('nameFr')(e.target.value)} maxLength={60} />}
          </Field>
        </div>

        <div className={styles.fieldRow}>
          <Field label="Description" hint="English, optional" problem={problems.descriptionEn}>
            {(props) => <textarea {...props} rows={3} value={form.descriptionEn} onChange={(e) => edit('descriptionEn')(e.target.value)} maxLength={240} />}
          </Field>
          <Field label="Description in French" hint="Optional" problem={problems.descriptionFr}>
            {(props) => (
              <textarea {...props} lang="fr" rows={3} value={form.descriptionFr} onChange={(e) => edit('descriptionFr')(e.target.value)} maxLength={240} />
            )}
          </Field>
        </div>

        <div className={styles.fieldRow} data-columns="3">
          <Field label="Category" problem={problems.categoryCode}>
            {(props) => (
              <select {...props} value={form.categoryCode} onChange={(e) => edit('categoryCode')(e.target.value)}>
                {categories.map((category) => (
                  <option key={category.category_code} value={category.category_code}>
                    {category.category_name}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label="Price" hint="Before tax, with the cheapest choices" problem={problems.price}>
            {(props) => (
              <span className={styles.prefixed}>
                <span aria-hidden="true">$</span>
                <input {...props} inputMode="decimal" placeholder="0.00" value={form.price} onChange={(e) => edit('price')(e.target.value)} required />
              </span>
            )}
          </Field>
          <Field label="Stock on hand" hint="Blank: not tracked, never sells out" problem={problems.stock}>
            {(props) => <input {...props} inputMode="numeric" value={form.stock} onChange={(e) => edit('stock')(e.target.value)} />}
          </Field>
        </div>

        <section className={styles.facts} aria-label="Set elsewhere">
          <h3 className={staff.sectionTitle}>Set in the menu file</h3>
          <dl>
            <dt>Choices</dt>
            <dd>
              {item && item.choices.length > 0 ? (
                <ul className={styles.choices}>
                  {item.choices.map((group) => (
                    <li key={group.group_name}>
                      <strong>{group.group_name}</strong>
                      {group.required ? ' (guest must choose)' : ''}: {optionsLabel(group)}
                    </li>
                  ))}
                </ul>
              ) : (
                'None. Guests order it as it is.'
              )}
            </dd>
            <dt>Allergens</dt>
            <dd>
              {item?.allergens_reviewed_on ? (
                `Reviewed ${item.allergens_reviewed_on}.`
              ) : (
                <span className={styles.warning}>
                  Not reviewed. The agent won't answer allergen questions about this item; it hands them to staff.
                </span>
              )}
            </dd>
            {item && (
              <>
                <dt>Code</dt>
                <dd>
                  <code>{item.item_code}</code> — used by the agent and on orders; it never changes.
                </dd>
              </>
            )}
          </dl>
        </section>

        <div className={styles.editorFoot}>
          <div className={styles.saveActions}>
            <button type="submit" className={`${staff.button} ${staff.buttonPrimary}`} disabled={busy || (item !== null && !dirty)}>
              {busy ? 'Saving…' : item ? 'Save changes' : 'Add to menu'}
            </button>
            <button type="button" className={staff.button} onClick={onClose} disabled={busy}>
              Cancel
            </button>
          </div>
          {item?.is_active && !confirmingRemove && (
            <button type="button" className={styles.dangerButton} onClick={() => setConfirmingRemove(true)} disabled={busy}>
              Remove from menu
            </button>
          )}
        </div>

        {confirmingRemove && item && (
          <div className={styles.confirm} role="group" aria-label="Remove from menu">
            <p>
              Take <strong>{item.name_en}</strong> off the menu? Guests and the agent stop seeing it from the next order. Past
              orders keep it, and you can put it back any time.
            </p>
            <div className={styles.saveActions}>
              <button type="button" className={styles.dangerButtonSolid} disabled={busy} onClick={() => void setActive(false)}>
                Remove from menu
              </button>
              <button type="button" className={staff.button} onClick={() => setConfirmingRemove(false)}>
                Keep it
              </button>
            </div>
          </div>
        )}
      </form>
    </aside>
  )
}

interface FieldProps {
  label: string
  hint?: string
  problem?: string
  children: (props: { id: string; 'aria-describedby'?: string; 'aria-invalid'?: boolean }) => ReactNode
}

function Field({ label, hint, problem, children }: FieldProps) {
  const id = useId()
  const describedBy = [hint && `${id}-hint`, problem && `${id}-problem`].filter(Boolean).join(' ') || undefined
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      {children({ id, 'aria-describedby': describedBy, 'aria-invalid': problem ? true : undefined })}
      {hint && (
        <span id={`${id}-hint`} className={styles.help}>
          {hint}
        </span>
      )}
      {problem && (
        <span id={`${id}-problem`} className={styles.fieldProblem}>
          {problem}
        </span>
      )}
    </div>
  )
}
