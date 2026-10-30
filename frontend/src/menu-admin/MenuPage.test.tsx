/** The menu page against a stand-in backend. */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'
import type { AdminCategory, AdminItem } from '../api/client'

const CATEGORIES: AdminCategory[] = [
  { category_code: 'burgers', category_name: 'Burgers' },
  { category_code: 'sides', category_name: 'Sides' },
]

function item(overrides: Partial<AdminItem>): AdminItem {
  return {
    item_code: 'fries',
    category_code: 'sides',
    name_en: 'Fries',
    name_fr: 'Frites',
    description_en: null,
    description_fr: null,
    base_price_cents: 249,
    is_active: true,
    stock: 100,
    sold_out: false,
    photo: { version: 'v1', credit: 'Mitchell Luo on Unsplash' },
    allergens_reviewed_on: '2026-10-01',
    allergens: { contains: [], may_contain: [] },
    option_allergens: [],
    sizes: [],
    choices: [],
    ...overrides,
  }
}

const ITEMS: AdminItem[] = [
  item({ item_code: 'classic_burger', category_code: 'burgers', name_en: 'Classic burger', name_fr: 'Hamburger classique', base_price_cents: 549 }),
  item({ item_code: 'fries' }),
  item({ item_code: 'onion_rings', name_en: 'Onion rings', name_fr: 'Rondelles d’oignon', photo: null, stock: 0, sold_out: true }),
]

interface Sent {
  method: string
  path: string
  body: unknown
}

const COLA = item({
  item_code: 'cola',
  name_en: 'Cola',
  name_fr: null,
  base_price_cents: 199,
  allergens_reviewed_on: null,
  allergens: null,
  sizes: [
    { name_en: 'Small', price_cents: 199, is_default: false },
    { name_en: 'Large', price_cents: 279, is_default: false },
  ],
})
const CHEESY = item({
  item_code: 'cheesy_melt',
  category_code: 'burgers',
  name_en: 'Cheesy melt',
  name_fr: null,
  option_allergens: [{ option_name: 'Cheese', contains: ['milk'], may_contain: [] }],
})

/** Keeps a menu and changes it the way the backend would. */
function installBackend(refuse: Record<string, { status: number; code: string }> = {}, extra: AdminItem[] = []) {
  let items = structuredClone([...ITEMS, ...extra])
  const sent: Sent[] = []
  const respond = (status: number, body: unknown) =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
  const replace = (updated: AdminItem) => {
    items = items.some((each) => each.item_code === updated.item_code)
      ? items.map((each) => (each.item_code === updated.item_code ? updated : each))
      : [...items, updated]
    return respond(updated === items.at(-1) && sent.at(-1)?.method === 'POST' ? 201 : 200, updated)
  }

  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^\/api/, '').split('?')[0]
      const method = init?.method ?? 'GET'
      const isJson = typeof init?.body === 'string'
      sent.push({ method, path, body: isJson ? JSON.parse(init.body as string) : init?.body })
      const refusal = refuse[`${method} ${path}`]
      if (refusal) return respond(refusal.status, { error: { code: refusal.code, message: refusal.code, details: {} } })

      if (method === 'GET' && path === '/admin/menu') return respond(200, { categories: CATEGORIES, items })
      if (method === 'POST' && path === '/admin/menu/items') {
        const body = sent.at(-1)!.body as Record<string, unknown>
        const code = String(body.name_en).toLowerCase().replace(/[^a-z0-9]+/g, '_')
        return replace(
          item({
            ...(body as Partial<AdminItem>),
            item_code: code,
            stock: (body.stock as number | null) ?? null,
            photo: null,
            allergens_reviewed_on: null,
            allergens: null,
            sizes: [],
          }),
        )
      }
      const match = /^\/admin\/menu\/items\/(\w+)(\/photo)?$/.exec(path)
      const found = items.find((each) => each.item_code === match?.[1])
      if (!match || !found) return respond(404, { error: { code: 'unknown_item', message: 'no', details: {} } })
      if (match[2] && method === 'PUT') return replace({ ...found, photo: { version: `v${sent.length}`, credit: null } })
      if (match[2] && method === 'DELETE') return replace({ ...found, photo: null })
      if (method === 'PATCH') {
        const changes = sent.at(-1)!.body as Partial<AdminItem>
        return replace({ ...found, ...changes, sizes: 'sizes' in changes ? (changes.sizes ?? []) : found.sizes })
      }
      return respond(405, {})
    }),
  )
  return { sent, writes: () => sent.filter((each) => each.method !== 'GET') }
}

function open() {
  window.history.replaceState(null, '', '/menu')
  render(<App />)
}

beforeEach(() => {
  // jsdom has neither; the editor previews a chosen photo with them.
  URL.createObjectURL = vi.fn(() => 'blob:preview')
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => vi.unstubAllGlobals())

describe('the menu page', () => {
  it('lists every item by category, with its photo or a placeholder', async () => {
    installBackend()
    open()

    const sides = await screen.findByRole('region', { name: /Sides/ })
    const rows = within(sides).getAllByRole('button')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Fries')
    expect(rows[0]).toHaveTextContent('Frites')
    expect(rows[0]).toHaveTextContent('$2.49')
    expect(rows[0].querySelector('img')).toHaveAttribute('src', '/api/menu/items/fries/photo?v=v1')
    // No photo: a placeholder in its place, and a flag saying so.
    expect(rows[1].querySelector('img')).toBeNull()
    expect(rows[1]).toHaveTextContent('No photo')
    expect(rows[1]).toHaveTextContent('Sold out')
    expect(screen.getByText(/3 on the menu/)).toBeInTheDocument()
  })

  it('marks an unlimited item sold out, and back', async () => {
    const backend = installBackend()
    const user = userEvent.setup()
    open()

    await user.click(await screen.findByText('Classic burger'))
    await user.click(screen.getByRole('radio', { name: 'Unlimited' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await screen.findByText(/^Saved/)
    expect(backend.writes().at(-1)?.body).toEqual({ stock: null })

    await user.click(screen.getByRole('radio', { name: 'Sold out' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(backend.writes().at(-1)?.body).toEqual({ stock: 0 }))
  })

  it('asks how many when an item is counted', async () => {
    const backend = installBackend()
    const user = userEvent.setup()
    open()

    await user.click(await screen.findByText('Classic burger'))
    const count = screen.getByRole('textbox', { name: 'How many do you have?' })
    await user.clear(count)
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(await screen.findByText('Enter how many you have, as a whole number.')).toBeInTheDocument()
    expect(backend.writes()).toHaveLength(0)
  })

  it('finds items by name in either language', async () => {
    installBackend()
    open()
    await screen.findByText('Classic burger')

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search items' }), 'rondelles')

    expect(screen.getByText('Onion rings')).toBeInTheDocument()
    expect(screen.queryByText('Fries')).not.toBeInTheDocument()
  })

  it('adds an item with a photo', async () => {
    const backend = installBackend()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Add item' }))

    const editor = screen.getByRole('complementary', { name: 'New item' })
    await userEvent.type(within(editor).getByLabelText('Name'), 'Spicy wrap')
    await userEvent.selectOptions(within(editor).getByLabelText('Category'), 'burgers')
    await userEvent.type(within(editor).getByLabelText('Price'), '6.9')
    await userEvent.upload(within(editor).getByLabelText('Photo file'), new File(['\xff\xd8\xff'], 'wrap.jpg', { type: 'image/jpeg' }))
    await userEvent.click(within(editor).getByRole('button', { name: 'Add to menu' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Added to the menu.')
    expect(backend.writes().map(({ method, path }) => `${method} ${path}`)).toEqual([
      'POST /admin/menu/items',
      'PUT /admin/menu/items/spicy_wrap/photo',
    ])
    expect(backend.writes()[0].body).toMatchObject({ name_en: 'Spicy wrap', category_code: 'burgers', base_price_cents: 690, stock: null })
    const burgers = screen.getByRole('region', { name: /Burgers/ })
    expect(within(burgers).getByText('Spicy wrap')).toBeInTheDocument()
    // Nobody reviewed its allergens yet, and the page says what that means.
    expect(within(burgers).getByText('Allergens not reviewed')).toBeInTheDocument()
    expect(screen.getByText(/won't answer allergen questions/)).toBeInTheDocument()
  })

  it('says what is wrong before sending anything', async () => {
    const backend = installBackend()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Add item' }))

    await userEvent.type(screen.getByLabelText('Price'), 'free')
    await userEvent.click(screen.getByRole('button', { name: 'Add to menu' }))

    expect(screen.getByText('Give the item a name.')).toBeInTheDocument()
    expect(screen.getByText('Enter a price, like 5.49.')).toBeInTheDocument()
    expect(screen.getByLabelText('Name')).toHaveAttribute('aria-invalid', 'true')
    expect(backend.writes()).toEqual([])
  })

  it('shows a name the backend refused on the name field', async () => {
    installBackend({ 'POST /admin/menu/items': { status: 409, code: 'duplicate_item_name' } })
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Add item' }))

    await userEvent.type(screen.getByLabelText('Name'), 'Fries')
    await userEvent.type(screen.getByLabelText('Price'), '3')
    await userEvent.click(screen.getByRole('button', { name: 'Add to menu' }))

    expect(await screen.findByText('Another item on the menu is already called “Fries”.')).toBeInTheDocument()
  })

  it('edits an item, sending only what changed', async () => {
    const backend = installBackend()
    open()
    await userEvent.click(await screen.findByRole('button', { name: /Fries/ }))

    const save = screen.getByRole('button', { name: 'Save changes' })
    expect(save).toBeDisabled()
    const price = screen.getByLabelText('Price')
    await userEvent.clear(price)
    await userEvent.type(price, '2.99')
    await userEvent.click(save)

    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(backend.writes()).toEqual([{ method: 'PATCH', path: '/admin/menu/items/fries', body: { base_price_cents: 299 } }])
    expect(screen.getAllByText('$2.99').length).toBeGreaterThan(0)
  })

  it('removes a photo only when saved, and shows the placeholder', async () => {
    const backend = installBackend()
    open()
    await userEvent.click(await screen.findByRole('button', { name: /Fries/ }))

    await userEvent.click(screen.getByRole('button', { name: 'Remove photo' }))
    expect(backend.writes()).toEqual([])
    expect(screen.getByRole('img', { name: 'No photo of Fries yet' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(backend.writes()).toEqual([{ method: 'DELETE', path: '/admin/menu/items/fries/photo', body: undefined }]))
  })

  it('takes an item off the menu after asking, and puts it back', async () => {
    const backend = installBackend()
    open()
    await userEvent.click(await screen.findByRole('button', { name: /Fries/ }))

    await userEvent.click(screen.getByRole('button', { name: 'Remove from menu' }))
    const confirm = screen.getByRole('group', { name: 'Remove from menu' })
    expect(confirm).toHaveTextContent('Past orders keep it')
    await userEvent.click(within(confirm).getByRole('button', { name: 'Remove from menu' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Taken off the menu.')
    expect(backend.writes().at(-1)).toEqual({ method: 'PATCH', path: '/admin/menu/items/fries', body: { is_active: false } })
    // The list now shows what was taken off, so it can be found again.
    expect(screen.getByRole('button', { name: /Taken off/, pressed: true })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Put back on the menu' }))

    expect(await screen.findByText('Back on the menu.')).toBeInTheDocument()
    expect(backend.writes().at(-1)?.body).toEqual({ is_active: true })
  })

  it('asks before throwing away changes that were not saved', async () => {
    installBackend()
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    open()
    await userEvent.click(await screen.findByRole('button', { name: /Fries/ }))
    await userEvent.type(screen.getByLabelText('Name in French'), ' frites')

    await userEvent.click(screen.getByRole('button', { name: /Classic burger/ }))

    expect(confirm).toHaveBeenCalled()
    expect(screen.getByRole('heading', { level: 2, name: 'Fries' })).toBeInTheDocument()
  })
})

describe('reviewing allergens on the menu page', () => {
  async function openItem(user: ReturnType<typeof userEvent.setup>, name: RegExp) {
    open()
    await user.click(await screen.findByRole('button', { name }))
    await screen.findByRole('button', { name: 'Close' })
  }

  const allergens = () => within(screen.getByRole('region', { name: 'Allergens' }))
  const patch = (backend: ReturnType<typeof installBackend>) => backend.writes().filter((each) => each.method === 'PATCH')

  it('shows what was reviewed and when', async () => {
    installBackend()
    const user = userEvent.setup()
    await openItem(user, /Fries/)

    expect(allergens().getByRole('radio', { name: 'None of these' })).toBeChecked()
    expect(allergens().getByText('Reviewed 2026-10-01.')).toBeInTheDocument()
  })

  it('changes a review and sends only that', async () => {
    const backend = installBackend()
    const user = userEvent.setup()
    await openItem(user, /Fries/)

    await user.click(allergens().getByRole('radio', { name: 'Has allergens' }))
    await user.click(within(allergens().getByRole('group', { name: 'Fries: contains' })).getByRole('button', { name: 'Mustard' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await screen.findByText(/^Saved/)
    expect(patch(backend)).toEqual([expect.objectContaining({ body: { allergens: { none_of_these: false, contains: ['mustard'], may_contain: [] } } })])
  })

  it('takes a review back as not reviewed, and warns what that means', async () => {
    const backend = installBackend()
    const user = userEvent.setup()
    await openItem(user, /Fries/)

    await user.click(allergens().getByRole('radio', { name: 'Not reviewed' }))
    expect(allergens().getByText(/it hands them to staff/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await screen.findByText(/^Saved/)
    expect(patch(backend)[0].body).toEqual({ allergens: null })
  })

  it('sends nothing when the list is opened and nothing ticked', async () => {
    const backend = installBackend({}, [COLA])
    const user = userEvent.setup()
    await openItem(user, /Cola/)

    await user.click(allergens().getByRole('radio', { name: 'Has allergens' }))
    await user.click(allergens().getByRole('button', { name: /Done with allergens/ }))

    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
    expect(backend.writes()).toEqual([])
  })

  it('won’t save a may contain with no reason', async () => {
    const backend = installBackend()
    const user = userEvent.setup()
    await openItem(user, /Fries/)

    await user.click(allergens().getByRole('radio', { name: 'Has allergens' }))
    await user.click(within(allergens().getByRole('group', { name: 'Fries: may contain' })).getByRole('button', { name: 'Milk' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(await allergens().findByRole('alert')).toHaveTextContent('Say why Fries may contain milk.')
    expect(backend.writes()).toEqual([])
  })

  it('leaves a review alone when something else changes', async () => {
    const backend = installBackend()
    const user = userEvent.setup()
    await openItem(user, /Fries/)

    await user.type(screen.getByRole('textbox', { name: /^Name in French/ }), ' maison')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await screen.findByText(/^Saved/)
    expect(patch(backend)[0].body).not.toHaveProperty('allergens')
  })

  it('reviews a new item as it is added, or leaves it unreviewed', async () => {
    const backend = installBackend()
    const user = userEvent.setup()
    open()
    await user.click(await screen.findByRole('button', { name: 'Add item' }))
    await user.type(screen.getByLabelText('Name'), 'Slaw')
    await user.type(screen.getByLabelText('Price'), '3')
    await user.click(screen.getByRole('button', { name: 'Add to menu' }))

    await screen.findByRole('status')
    expect(backend.writes()[0].body).toMatchObject({ name_en: 'Slaw', allergens: null, sizes: null })

    await user.click(screen.getByRole('button', { name: 'Add item' }))
    await user.type(screen.getByLabelText('Name'), 'Lemonade')
    await user.type(screen.getByLabelText('Price'), '2')
    await user.click(allergens().getByRole('radio', { name: 'None of these' }))
    await user.click(screen.getByRole('button', { name: 'Add to menu' }))

    await waitFor(() => expect(backend.writes()).toHaveLength(2))
    expect(backend.writes()[1].body).toMatchObject({ name_en: 'Lemonade', allergens: { none_of_these: true } })
  })

  it('shows what choices add, which stay in the menu file', async () => {
    installBackend({}, [CHEESY])
    const user = userEvent.setup()
    await openItem(user, /Cheesy/)

    expect(within(allergens().getByRole('list', { name: 'What choices add' })).getByText('Cheese adds milk')).toBeInTheDocument()
  })
})

describe('sizes on the menu page', () => {
  async function openItem(user: ReturnType<typeof userEvent.setup>, name: RegExp) {
    open()
    await user.click(await screen.findByRole('button', { name }))
    await screen.findByRole('button', { name: 'Close' })
  }

  const patch = (backend: ReturnType<typeof installBackend>) => backend.writes().filter((each) => each.method === 'PATCH')

  it('shows an item’s sizes at their real prices, in place of one price', async () => {
    installBackend({}, [COLA])
    const user = userEvent.setup()
    await openItem(user, /Cola/)

    expect(screen.queryByRole('textbox', { name: /^Price/ })).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Size 1 name' })).toHaveValue('Small')
    expect(screen.getByRole('textbox', { name: 'Size 2 price' })).toHaveValue('2.79')
    expect(screen.getByRole('combobox', { name: 'If a guest doesn’t say' })).toHaveDisplayValue('The agent asks')
  })

  it('adds sizes to an item with one price', async () => {
    const backend = installBackend()
    const user = userEvent.setup()
    await openItem(user, /Fries/)

    await user.click(screen.getByRole('button', { name: 'Add sizes' }))
    expect(screen.getByRole('textbox', { name: 'Size 1 price' })).toHaveValue('2.49')
    await user.type(screen.getByRole('textbox', { name: 'Size 1 name' }), 'Small')
    await user.type(screen.getByRole('textbox', { name: 'Size 2 name' }), 'Large')
    await user.type(screen.getByRole('textbox', { name: 'Size 2 price' }), '3.79')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await screen.findByText(/^Saved/)
    expect(patch(backend)[0].body).toEqual({
      sizes: [
        { name_en: 'Small', price_cents: 249, is_default: false },
        { name_en: 'Large', price_cents: 379, is_default: false },
      ],
    })
  })

  it('sends the remaining sizes when one is removed, and the new price', async () => {
    const backend = installBackend({}, [COLA])
    const user = userEvent.setup()
    await openItem(user, /Cola/)

    await user.click(screen.getByRole('button', { name: 'Add a size' }))
    await user.type(screen.getByRole('textbox', { name: 'Size 3 name' }), 'Huge')
    await user.type(screen.getByRole('textbox', { name: 'Size 3 price' }), '1.49')
    await user.click(screen.getByRole('button', { name: 'Remove Small' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await screen.findByText(/^Saved/)
    expect(patch(backend)[0].body).toEqual({
      base_price_cents: 149,
      sizes: [
        { name_en: 'Large', price_cents: 279, is_default: false },
        { name_en: 'Huge', price_cents: 149, is_default: false },
      ],
    })
  })

  it('goes back to one price', async () => {
    const backend = installBackend({}, [COLA])
    const user = userEvent.setup()
    await openItem(user, /Cola/)

    await user.click(screen.getByRole('button', { name: 'Use one price' }))
    expect(screen.getByRole('textbox', { name: /^Price/ })).toHaveValue('1.99')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await screen.findByText(/^Saved/)
    expect(patch(backend)[0].body).toEqual({ sizes: null })
  })

  it('moves the default', async () => {
    const backend = installBackend({}, [COLA])
    const user = userEvent.setup()
    await openItem(user, /Cola/)

    await user.selectOptions(screen.getByRole('combobox', { name: 'If a guest doesn’t say' }), 'Large')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await screen.findByText(/^Saved/)
    expect(patch(backend)[0].body).toEqual({
      sizes: [
        { name_en: 'Small', price_cents: 199, is_default: false },
        { name_en: 'Large', price_cents: 279, is_default: true },
      ],
    })
  })

  it('points a special’s refusal at the sizes', async () => {
    installBackend({ 'PATCH /admin/menu/items/cola': { status: 422, code: 'price_below_special' } }, [COLA])
    const user = userEvent.setup()
    await openItem(user, /Cola/)

    await user.selectOptions(screen.getByRole('combobox', { name: 'If a guest doesn’t say' }), 'Large')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(await screen.findByText('A special takes at least this much off the item. Set a higher price.')).toBeInTheDocument()
  })
})
