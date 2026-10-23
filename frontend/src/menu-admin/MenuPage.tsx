import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useRef, useState } from 'react'

import { api } from '../api/client'
import type { AdminItem, AdminMenu } from '../api/client'
import { INSIGHTS_PATH, LIVE_PATH, STAFF_PATH } from '../app/navigation'
import { PageLink } from '../app/PageLink'
import { money } from '../session/presentation'
import staff from '../staff/Staff.module.css'
import { ItemEditor } from './ItemEditor'
import { ItemPicture } from './ItemPicture'
import styles from './MenuAdmin.module.css'
import { stockLabel } from './words'

const MENU_KEY = ['admin-menu']

type Selection = { kind: 'none' } | { kind: 'new' } | { kind: 'item'; code: string }

/**
 * The menu page: every item with its photo, and an editor beside the list to
 * add one, change one, or take one off the menu.
 */
export function MenuPage() {
  const queryClient = useQueryClient()
  const menu = useQuery({ queryKey: MENU_KEY, queryFn: api.adminMenu, retry: false })
  const [selection, setSelection] = useState<Selection>({ kind: 'none' })
  const [notice, setNotice] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('all')
  const [showRemoved, setShowRemoved] = useState(false)
  const dirty = useRef(false)
  const onDirtyChange = useCallback((value: boolean) => {
    dirty.current = value
  }, [])

  const categories = menu.data?.categories ?? []
  const items = menu.data?.items ?? []
  const removedCount = items.filter((item) => !item.is_active).length
  const selected = selection.kind === 'item' ? (items.find((item) => item.item_code === selection.code) ?? null) : null
  const editing = selection.kind === 'new' || selected !== null

  /** Move to another item, or close, without losing typing by accident. */
  function select(next: Selection) {
    if (dirty.current && !window.confirm('Discard the changes you have not saved?')) return
    dirty.current = false
    setNotice(null)
    setSelection(next)
  }

  function saved(item: AdminItem, message: string) {
    queryClient.setQueryData<AdminMenu>(MENU_KEY, (current) => {
      if (!current) return current
      const known = current.items.some((each) => each.item_code === item.item_code)
      return {
        ...current,
        items: known ? current.items.map((each) => (each.item_code === item.item_code ? item : each)) : [...current.items, item],
      }
    })
    // The list's order and stock come from the backend.
    void queryClient.invalidateQueries({ queryKey: MENU_KEY })
    if (item.is_active === showRemoved) setShowRemoved(!item.is_active)
    setNotice(message)
    setSelection({ kind: 'item', code: item.item_code })
  }

  const needle = search.trim().toLowerCase()
  const shown = items.filter(
    (item) =>
      item.is_active !== showRemoved &&
      (category === 'all' || item.category_code === category) &&
      (!needle || [item.name_en, item.name_fr ?? '', item.item_code].some((text) => text.toLowerCase().includes(needle))),
  )
  const groups = categories
    .map((each) => ({ ...each, items: shown.filter((item) => item.category_code === each.category_code) }))
    .filter((group) => group.items.length > 0)

  return (
    <div className={staff.screen}>
      <header className={staff.topBar}>
        <h1 className={staff.title}>
          Menu <span className={staff.titleSub}>Items and photos</span>
        </h1>
        <nav className={staff.pageLinks} aria-label="Pages">
          <PageLink to={STAFF_PATH} className={staff.button}>
            Staff
          </PageLink>
          <PageLink to={INSIGHTS_PATH} className={staff.button}>
            Insights
          </PageLink>
          <PageLink to={LIVE_PATH} className={staff.button}>
            Live session
          </PageLink>
        </nav>
      </header>

      {menu.isError && (
        <p role="alert" className={staff.banner}>
          Cannot reach the backend. Start it with `make dev`, then reload.
        </p>
      )}

      <main className={styles.main} data-editing={editing}>
        <section className={styles.listPane} aria-labelledby="menu-items-title">
          <div className={styles.toolbar}>
            <div className={styles.toolbarHead}>
              <div>
                <h2 id="menu-items-title" className={styles.heading}>
                  Menu items
                </h2>
                <p className={styles.lede}>
                  {items.length - removedCount} on the menu
                  {removedCount > 0 && `, ${removedCount} taken off`}. Changes reach guests and the agent from the next order.
                </p>
              </div>
              <button type="button" className={`${staff.button} ${staff.buttonPrimary}`} onClick={() => select({ kind: 'new' })} disabled={!menu.data}>
                Add item
              </button>
            </div>

            <div className={styles.filters}>
              <label className={styles.search}>
                <span className={styles.visuallyHidden}>Search items</span>
                <input type="search" placeholder="Search items" value={search} onChange={(event) => setSearch(event.target.value)} />
              </label>
              <div className={staff.tabs} role="group" aria-label="Which items">
                <button type="button" className={staff.tab} aria-pressed={!showRemoved} onClick={() => setShowRemoved(false)}>
                  On the menu
                </button>
                <button type="button" className={staff.tab} aria-pressed={showRemoved} onClick={() => setShowRemoved(true)}>
                  Taken off<span className={staff.count}>{removedCount}</span>
                </button>
              </div>
            </div>

            <div className={styles.chips} role="group" aria-label="Category">
              {[{ category_code: 'all', category_name: 'All' }, ...categories].map((each) => (
                <button
                  key={each.category_code}
                  type="button"
                  className={styles.chip}
                  aria-pressed={category === each.category_code}
                  onClick={() => setCategory(each.category_code)}
                >
                  {each.category_name}
                </button>
              ))}
            </div>
          </div>

          {menu.isLoading ? (
            <p className={styles.empty}>Loading the menu…</p>
          ) : groups.length === 0 ? (
            <p className={styles.empty}>
              {showRemoved && !needle && category === 'all'
                ? 'Nothing has been taken off the menu.'
                : 'No items match. Try another search or category.'}
            </p>
          ) : (
            groups.map((group) => (
              <section key={group.category_code} className={styles.group} aria-labelledby={`group-${group.category_code}`}>
                <h3 id={`group-${group.category_code}`} className={styles.groupTitle}>
                  {group.category_name} <span className={styles.groupCount}>{group.items.length}</span>
                </h3>
                <ul className={styles.rows}>
                  {group.items.map((item) => (
                    <li key={item.item_code}>
                      <ItemRow
                        item={item}
                        current={selected?.item_code === item.item_code}
                        onOpen={() => select({ kind: 'item', code: item.item_code })}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            ))
          )}
        </section>

        {editing && menu.data && (
          <ItemEditor
            key={selected?.item_code ?? 'new'}
            item={selected}
            categories={categories}
            defaultCategory={category !== 'all' ? category : (categories[0]?.category_code ?? '')}
            notice={notice}
            onSaved={saved}
            onClose={() => select({ kind: 'none' })}
            onDirtyChange={onDirtyChange}
          />
        )}
      </main>
    </div>
  )
}

function ItemRow({ item, current, onOpen }: { item: AdminItem; current: boolean; onOpen: () => void }) {
  const flags = [
    !item.is_active && 'Taken off',
    item.is_active && item.sold_out && 'Sold out',
    !item.photo && 'No photo',
    !item.allergens_reviewed_on && 'Allergens not reviewed',
  ].filter((flag): flag is string => Boolean(flag))

  return (
    <button type="button" className={styles.row} aria-current={current} onClick={onOpen}>
      <ItemPicture src={item.photo ? api.photoUrl(item.item_code, item.photo.version) : null} categoryCode={item.category_code} size="thumb" />
      <span className={styles.rowText}>
        <span className={styles.rowName}>{item.name_en}</span>
        {item.name_fr && (
          <span className={styles.rowFrench} lang="fr">
            {item.name_fr}
          </span>
        )}
        {flags.length > 0 && (
          <span className={styles.flags}>
            {flags.map((flag) => (
              <span key={flag} className={styles.flag}>
                {flag}
              </span>
            ))}
          </span>
        )}
      </span>
      <span className={styles.rowNumbers}>
        <span className={styles.rowPrice}>{money(item.base_price_cents)}</span>
        <span className={styles.rowStock} data-sold-out={item.sold_out}>
          {stockLabel(item)}
        </span>
      </span>
    </button>
  )
}
