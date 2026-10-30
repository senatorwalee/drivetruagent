/** The onboarding page against a stand-in backend. */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { UserEvent } from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'
import type { AdminCategory, AdminItem, Combo, Onboarding } from '../api/client'
import { agentInfo } from '../test/fixtures'

const CATEGORIES: AdminCategory[] = [
  { category_code: 'burgers', category_name: 'Burgers' },
  { category_code: 'sides', category_name: 'Sides' },
  { category_code: 'drinks', category_name: 'Drinks' },
]
const ON_THE_MENU = [
  { item_code: 'classic_burger', category_code: 'burgers', name_en: 'Classic burger', base_price_cents: 549, is_active: true },
  { item_code: 'fries', category_code: 'sides', name_en: 'Fries', base_price_cents: 249, is_active: true },
] as AdminItem[]

interface Backend {
  sent: () => Onboarding[]
}

function installBackend({ items = ON_THE_MENU, combos = [] as Combo[], refuse = null as { status: number; code: string } | null } = {}): Backend {
  const sent: Onboarding[] = []
  const respond = (status: number, body: unknown) =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^\/api/, '').split('?')[0]
      if (path === '/admin/menu') return respond(200, { categories: items.length ? CATEGORIES : [], items })
      if (path === '/combos') return respond(200, combos)
      if (path === '/agent') return respond(200, agentInfo)
      if (path === '/admin/onboarding' && init?.method === 'POST') {
        if (refuse) return respond(refuse.status, { error: { code: refuse.code, message: refuse.code, details: {} } })
        const body = JSON.parse(init.body as string) as Onboarding
        sent.push(body)
        return respond(201, {
          restaurant_name: body.restaurant.name,
          items: body.items.length,
          sections_added: 0,
          combos: body.combos?.length ?? 0,
          diet_reviews: 0,
          allergen_reviews: body.items.filter((item) => item.allergens).length,
          items_taken_off: body.replace_menu ? items.length : 0,
          combos_retired: 0,
        })
      }
      return respond(404, {})
    }),
  )
  return { sent: () => sent }
}

function open() {
  window.history.replaceState(null, '', '/onboarding')
  render(<App />)
}

async function fillRestaurant(user: UserEvent, name = 'Harbour Grill') {
  await user.type(screen.getByRole('textbox', { name: 'Restaurant name' }), name)
  await user.type(screen.getByRole('textbox', { name: 'Address' }), '12 Water Street')
  await user.click(screen.getByRole('button', { name: 'Continue' }))
  await screen.findByRole('heading', { name: 'Enter your menu' })
}

async function addItem(user: UserEvent, name: string, price: string, section?: string) {
  if (section) await user.click(screen.getByRole('button', { name: section }))
  await user.type(screen.getByRole('textbox', { name: 'Item name' }), name)
  await user.type(screen.getByRole('textbox', { name: 'Price' }), `${price}{Enter}`)
  await screen.findByRole('button', { name: `Edit ${name}` })
}

const step = (name: string) => within(screen.getByRole('navigation', { name: 'Steps' })).getByRole('button', { name: new RegExp(name) })

afterEach(() => vi.unstubAllGlobals())

describe('onboarding', () => {
  it('takes a restaurant from its name to live', async () => {
    const backend = installBackend({ items: [] })
    const user = userEvent.setup()
    open()

    await fillRestaurant(user)
    await addItem(user, 'Fish taco', '6.50', 'Mains')
    await addItem(user, 'Slaw', '3', 'Sides')
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    await screen.findByRole('heading', { name: 'Allergens' })
    await user.click(within(screen.getByRole('radiogroup', { name: 'Allergens in Fish taco' })).getByRole('radio', { name: 'None of these' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    await screen.findByRole('heading', { name: 'Halal and kosher' })
    await user.click(within(screen.getByRole('radiogroup', { name: 'Fish taco for halal' })).getByRole('radio', { name: 'Suitable' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    await screen.findByRole('heading', { name: 'Combos' })
    await user.click(screen.getByRole('button', { name: 'Add a combo' }))
    await user.type(screen.getByRole('textbox', { name: 'Combo name' }), 'Taco plate')
    await user.click(screen.getByRole('button', { name: '+ Mains' }))
    await user.click(screen.getByRole('button', { name: '+ Sides' }))
    await user.type(screen.getByRole('textbox', { name: 'The guest saves' }), '1.50')
    expect(screen.getByRole('status')).toHaveTextContent('With the combo, the guest pays $8.00.')
    await user.click(screen.getByRole('button', { name: 'Add combo' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    await screen.findByRole('heading', { name: 'Ready to go live?' })
    expect(screen.getByRole('region', { name: 'Restaurant' })).toHaveTextContent('Harbour Grill')
    expect(screen.getByRole('region', { name: 'Menu' })).toHaveTextContent('2 items')
    await user.click(screen.getByRole('button', { name: 'Go live' }))

    expect(await screen.findByRole('heading', { name: 'Harbour Grill is live.' })).toBeInTheDocument()
    expect(screen.getByText(/2 items and 1 combo/)).toBeInTheDocument()
    expect(backend.sent()).toEqual([
      {
        restaurant: { name: 'Harbour Grill', address: '12 Water Street', pickup_minutes: 15 },
        replace_menu: false,
        items: [
          expect.objectContaining({
            section: 'Mains',
            name_en: 'Fish taco',
            base_price_cents: 650,
            stock: null,
            diets: { halal: { status: 'suitable', note: null } },
            allergens: { none_of_these: true },
          }),
          expect.objectContaining({ section: 'Sides', name_en: 'Slaw', base_price_cents: 300, stock: null, diets: {}, allergens: null }),
        ],
        combos: [{ name_en: 'Taco plate', discount_cents: 150, slots: ['Mains', 'Sides'] }],
      },
    ])
  })

  it('won’t leave the first step without a name', async () => {
    installBackend()
    const user = userEvent.setup()
    open()

    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Enter the restaurant’s name.')
    expect(screen.getByRole('heading', { name: 'Tell us about your restaurant' })).toBeInTheDocument()
  })

  it('moves on with Enter, as a form does', async () => {
    installBackend()
    const user = userEvent.setup()
    open()

    await user.type(screen.getByRole('textbox', { name: 'Restaurant name' }), 'Harbour Grill{Enter}')

    expect(await screen.findByRole('heading', { name: 'Enter your menu' })).toBeInTheDocument()
  })

  it('keeps everything through a reload', async () => {
    installBackend()
    const user = userEvent.setup()
    open()
    await fillRestaurant(user)
    await addItem(user, 'Fish taco', '6.50')

    // A reload: the page is gone and comes back.
    document.body.innerHTML = ''
    open()

    expect(await screen.findByRole('heading', { name: 'Enter your menu' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit Fish taco' })).toBeInTheDocument()
  })

  it('starts over only when asked twice', async () => {
    installBackend()
    const user = userEvent.setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    open()
    await fillRestaurant(user)

    await user.click(screen.getByRole('button', { name: 'Start over' }))
    expect(screen.getByRole('heading', { name: 'Enter your menu' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Start over' }))
    expect(screen.getByRole('heading', { name: 'Tell us about your restaurant' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Restaurant name' })).toHaveValue('')
    confirm.mockRestore()
  })

  it('can jump to any step from the rail', async () => {
    installBackend()
    const user = userEvent.setup()
    open()

    await user.click(step('Combos'))

    expect(await screen.findByRole('heading', { name: 'Combos' })).toBeInTheDocument()
    expect(screen.getByText('Add your menu first, then come back to this.')).toBeInTheDocument()
  })
})

describe('entering items', () => {
  async function onMenuStep(user: UserEvent) {
    open()
    await fillRestaurant(user)
  }

  it('keeps the section and starts the next item, ready to type', async () => {
    installBackend()
    const user = userEvent.setup()
    await onMenuStep(user)

    await addItem(user, 'Fish burger', '6.50', 'Burgers')

    const name = screen.getByRole('textbox', { name: 'Item name' })
    expect(name).toHaveValue('')
    expect(name).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Burgers' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('status')).toHaveTextContent('Added Fish burger.')
  })

  it('takes a counted quantity', async () => {
    const backend = installBackend({ items: [] })
    const user = userEvent.setup()
    await onMenuStep(user)

    await user.click(screen.getByRole('radio', { name: 'Counted' }))
    await user.type(screen.getByRole('textbox', { name: 'Item name' }), 'Lobster roll')
    await user.type(screen.getByRole('textbox', { name: 'Price' }), '14')
    await user.click(screen.getByRole('button', { name: 'Add item' }))
    expect(await screen.findByText('Enter how many you have, as a whole number.')).toBeInTheDocument()

    await user.type(screen.getByRole('textbox', { name: 'How many do you have?' }), '12{Enter}')

    expect(await screen.findByRole('button', { name: 'Edit Lobster roll' })).toHaveTextContent('12 on hand')
    await user.click(step('Go live'))
    await user.click(await screen.findByRole('button', { name: 'Go live' }))
    await waitFor(() => expect(backend.sent()[0].items[0].stock).toBe(12))
  })

  it('offers no sold out for an item that hasn’t been on sale yet', async () => {
    installBackend()
    const user = userEvent.setup()
    await onMenuStep(user)

    expect(screen.getByRole('radio', { name: 'Unlimited' })).toBeChecked()
    expect(screen.queryByRole('radio', { name: 'Sold out' })).not.toBeInTheDocument()
  })

  it('adds a section of the restaurant’s own', async () => {
    installBackend()
    const user = userEvent.setup()
    await onMenuStep(user)

    await user.click(screen.getByRole('button', { name: 'New section' }))
    await user.type(screen.getByRole('textbox', { name: 'New section name' }), 'Wraps{Enter}')

    expect(screen.getByRole('button', { name: 'Wraps' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('button', { name: 'Edit Wraps' })).not.toBeInTheDocument()
    await addItem(user, 'Veggie wrap', '7')
    expect(within(screen.getByRole('region', { name: 'Wraps' })).getByRole('button', { name: 'Edit Veggie wrap' })).toBeInTheDocument()
  })

  it('edits and removes an item', async () => {
    installBackend()
    const user = userEvent.setup()
    await onMenuStep(user)
    await addItem(user, 'Fish burger', '6.50')

    await user.click(screen.getByRole('button', { name: 'Edit Fish burger' }))
    const price = screen.getByRole('textbox', { name: 'Price' })
    expect(price).toHaveValue('6.50')
    await user.clear(price)
    await user.type(price, '7{Enter}')
    expect(await screen.findByRole('button', { name: 'Edit Fish burger' })).toHaveTextContent('$7.00')

    await user.click(screen.getByRole('button', { name: 'Remove Fish burger' }))
    expect(screen.queryByRole('button', { name: 'Edit Fish burger' })).not.toBeInTheDocument()
  })

  it('tells the restaurant it can add to its menu or start fresh', async () => {
    const backend = installBackend()
    const user = userEvent.setup()
    await onMenuStep(user)

    expect(await screen.findByText('2 items')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Add to it' })).toBeChecked()

    await user.type(screen.getByRole('textbox', { name: 'Item name' }), 'Fries')
    await user.type(screen.getByRole('textbox', { name: 'Price' }), '3{Enter}')
    expect(await screen.findByText(/already on your menu/)).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'Start fresh' }))
    await user.click(screen.getByRole('button', { name: 'Add item' }))
    await screen.findByRole('button', { name: 'Edit Fries' })

    await user.click(step('Go live'))
    expect(await screen.findByText(/This replaces your menu/)).toHaveTextContent('The 2 items on it now move to Taken off')
    await user.click(screen.getByRole('button', { name: 'Replace the menu and go live' }))

    await screen.findByRole('heading', { name: 'Harbour Grill is live.' })
    expect(backend.sent()[0].replace_menu).toBe(true)
    expect(screen.getByText(/2 items moved to Taken off/)).toBeInTheDocument()
  })

  it('doesn’t mention a menu to a restaurant that has none', async () => {
    installBackend({ items: [] })
    const user = userEvent.setup()
    await onMenuStep(user)

    expect(screen.queryByRole('radiogroup', { name: 'What to do with them' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mains' })).toBeInTheDocument()
  })
})

describe('halal and kosher', () => {
  async function onDietStep(user: UserEvent) {
    open()
    await fillRestaurant(user)
    await addItem(user, 'Fish burger', '6.50', 'Burgers')
    await addItem(user, 'Veggie burger', '6')
    await user.click(step('Halal and kosher'))
    await screen.findByRole('heading', { name: 'Halal and kosher' })
  }

  const row = (name: string) => screen.getByRole('radiogroup', { name })

  it('keeps each diet apart, and counts what has been reviewed', async () => {
    installBackend()
    const user = userEvent.setup()
    await onDietStep(user)

    await user.click(within(row('Fish burger for halal')).getByRole('radio', { name: 'Not suitable' }))
    const diets = screen.getByRole('group', { name: 'Diet' })
    expect(within(diets).getByRole('button', { name: /Halal/ })).toHaveTextContent('1 of 2')
    expect(within(diets).getByRole('button', { name: /Kosher/ })).toHaveTextContent('0 of 2')

    await user.click(within(diets).getByRole('button', { name: /Kosher/ }))
    expect(within(row('Fish burger for kosher')).getByRole('radio', { name: 'Not reviewed' })).toBeChecked()
  })

  it('needs a name for a certificate, and can’t move on without one', async () => {
    const backend = installBackend()
    const user = userEvent.setup()
    await onDietStep(user)

    await user.click(within(row('Fish burger for halal')).getByRole('radio', { name: 'Certified' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Say who certified Fish burger as halal.')

    await user.type(screen.getByRole('textbox', { name: 'Who certified Fish burger' }), 'HMA')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await screen.findByRole('heading', { name: 'Combos' })

    await user.click(step('Go live'))
    await user.click(await screen.findByRole('button', { name: 'Go live' }))
    await waitFor(() => expect(backend.sent()[0].items[0].diets).toEqual({ halal: { status: 'certified', certified_by: 'HMA' } }))
  })

  it('takes a reason in words a guest can hear', async () => {
    const backend = installBackend()
    const user = userEvent.setup()
    await onDietStep(user)

    await user.click(within(row('Fish burger for halal')).getByRole('radio', { name: 'Suitable' }))
    await user.click(screen.getByRole('button', { name: 'Add a reason' }))
    await user.type(screen.getByRole('textbox', { name: 'Why, for Fish burger' }), 'Fried in its own oil')

    await user.click(step('Go live'))
    await user.click(await screen.findByRole('button', { name: 'Go live' }))
    await waitFor(() => expect(backend.sent()[0].items[0].diets?.halal).toEqual({ status: 'suitable', note: 'Fried in its own oil' }))
  })

  it('sets a whole section at once', async () => {
    installBackend()
    const user = userEvent.setup()
    await onDietStep(user)

    const burgers = screen.getByRole('region', { name: 'Burgers' })
    await user.click(within(burgers).getByRole('button', { name: 'Suitable' }))

    expect(within(row('Fish burger for halal')).getByRole('radio', { name: 'Suitable' })).toBeChecked()
    expect(within(row('Veggie burger for halal')).getByRole('radio', { name: 'Suitable' })).toBeChecked()
  })

  it('can be skipped, and then claims nothing', async () => {
    const backend = installBackend()
    const user = userEvent.setup()
    await onDietStep(user)

    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(await screen.findByRole('region', { name: 'Halal and kosher' })).toHaveTextContent('Not reviewed. The agent makes no claims.')
    await user.click(screen.getByRole('button', { name: 'Go live' }))
    await waitFor(() => expect(backend.sent()[0].items[0].diets).toEqual({}))
  })
})

describe('combos', () => {
  async function onCombosStep(user: UserEvent) {
    open()
    await fillRestaurant(user)
    await addItem(user, 'Fish taco', '6.50', 'Burgers')
    await addItem(user, 'Slaw', '3', 'Sides')
    await user.click(step('Combos'))
    await screen.findByRole('heading', { name: 'Combos' })
    await user.click(screen.getByRole('button', { name: 'Add a combo' }))
  }

  it('shows which items count, the main one first', async () => {
    installBackend()
    const user = userEvent.setup()
    await onCombosStep(user)

    await user.click(screen.getByRole('button', { name: '+ Burgers' }))
    await user.click(screen.getByRole('button', { name: '+ Sides' }))

    const [main, side] = within(screen.getByRole('form', { name: 'New combo' })).getAllByRole('listitem')
    // Classic burger is on the menu too, and stays when adding.
    expect(main).toHaveTextContent('MainBurgersAny of 2, from $5.49')
    expect(side).toHaveTextContent('SidesAny of 2, from $2.49')
  })

  it('won’t make a combo that gives food away', async () => {
    installBackend({ items: [] })
    const user = userEvent.setup()
    open()
    await fillRestaurant(user)
    await addItem(user, 'Fish taco', '6.50', 'Mains')
    await addItem(user, 'Slaw', '3', 'Sides')
    await user.click(step('Combos'))
    await user.click(await screen.findByRole('button', { name: 'Add a combo' }))

    await user.type(screen.getByRole('textbox', { name: 'Combo name' }), 'Too generous')
    await user.click(screen.getByRole('button', { name: '+ Mains' }))
    await user.click(screen.getByRole('button', { name: '+ Sides' }))
    await user.type(screen.getByRole('textbox', { name: 'The guest saves' }), '9.50')
    await user.click(screen.getByRole('button', { name: 'Add combo' }))

    expect(screen.getByRole('alert')).toHaveTextContent('The cheapest set costs $9.50, so the saving has to be less than that.')
    expect(screen.queryByRole('button', { name: 'Edit Too generous' })).not.toBeInTheDocument()
  })

  it('needs two parts', async () => {
    installBackend()
    const user = userEvent.setup()
    await onCombosStep(user)

    await user.type(screen.getByRole('textbox', { name: 'Combo name' }), 'One thing')
    await user.click(screen.getByRole('button', { name: '+ Burgers' }))
    await user.click(screen.getByRole('button', { name: 'Add combo' }))

    expect(screen.getByRole('alert')).toHaveTextContent('A combo needs at least two parts.')
  })

  it('edits and removes a combo', async () => {
    installBackend()
    const user = userEvent.setup()
    await onCombosStep(user)
    await user.type(screen.getByRole('textbox', { name: 'Combo name' }), 'Plate')
    await user.click(screen.getByRole('button', { name: '+ Burgers' }))
    await user.click(screen.getByRole('button', { name: '+ Sides' }))
    await user.type(screen.getByRole('textbox', { name: 'The guest saves' }), '1')
    await user.click(screen.getByRole('button', { name: 'Add combo' }))

    await user.click(await screen.findByRole('button', { name: 'Edit Plate' }))
    const saving = screen.getByRole('textbox', { name: 'The guest saves' })
    expect(saving).toHaveValue('1.00')
    await user.clear(saving)
    await user.type(saving, '2')
    await user.click(screen.getByRole('button', { name: 'Save combo' }))
    expect(await screen.findByText('Saves $2.00')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Remove Plate' }))
    expect(screen.queryByText('Saves $2.00')).not.toBeInTheDocument()
  })
})

describe('going live', () => {
  it('lists what is missing, and holds the button until it is done', async () => {
    installBackend()
    const user = userEvent.setup()
    open()

    await user.click(step('Go live'))

    const blockers = await screen.findByRole('list', { name: 'Before you can go live' })
    expect(blockers).toHaveTextContent('Enter the restaurant’s name.')
    expect(blockers).toHaveTextContent('Add at least one item.')
    expect(screen.getByRole('button', { name: 'Go live' })).toBeDisabled()

    await user.click(within(blockers).getByRole('button', { name: 'Go to Menu' }))
    expect(await screen.findByRole('heading', { name: 'Enter your menu' })).toBeInTheDocument()
  })

  it('stays put and keeps everything if the backend refuses', async () => {
    installBackend({ refuse: { status: 409, code: 'duplicate_item_name' } })
    const user = userEvent.setup()
    open()
    await fillRestaurant(user)
    await addItem(user, 'Fish taco', '6.50')

    await user.click(step('Go live'))
    await user.click(await screen.findByRole('button', { name: 'Go live' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('already on your menu')
    expect(screen.getByRole('heading', { name: 'Ready to go live?' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Menu' })).toHaveTextContent('1 item')
  })

  it('starts clean after going live', async () => {
    installBackend()
    const user = userEvent.setup()
    open()
    await fillRestaurant(user)
    await addItem(user, 'Fish taco', '6.50')
    await user.click(step('Go live'))
    await user.click(await screen.findByRole('button', { name: 'Go live' }))
    await screen.findByRole('heading', { name: 'Harbour Grill is live.' })

    expect(screen.getByRole('link', { name: 'Try it in the live lane' })).toHaveAttribute('href', '/live')
    expect(screen.getByRole('link', { name: 'Add photos on the Menu page' })).toHaveAttribute('href', '/menu')
    expect(JSON.parse(window.localStorage.getItem('frontdesk.onboarding.draft') ?? '{}').items).toEqual([])
  })
})

describe('the allergens step', () => {
  async function onAllergens(user: UserEvent, ...names: string[]) {
    open()
    await fillRestaurant(user)
    for (const name of names) await addItem(user, name, '5')
    await user.click(step('Allergens'))
    await screen.findByRole('heading', { name: 'Allergens' })
  }

  const answer = (item: string, choice: string) => within(screen.getByRole('radiogroup', { name: `Allergens in ${item}` })).getByRole('radio', { name: choice })
  const chip = (item: string, level: string, allergen: string) => within(screen.getByRole('group', { name: `${item}: ${level}` })).getByRole('button', { name: allergen })

  async function goLive(user: UserEvent, backend: Backend) {
    await user.click(step('Go live'))
    await user.click(await screen.findByRole('button', { name: 'Go live' }))
    await waitFor(() => expect(backend.sent()).toHaveLength(1))
    return backend.sent()[0].items
  }

  it('starts every item not reviewed', async () => {
    installBackend({ items: [] })
    const user = userEvent.setup()
    await onAllergens(user, 'Fish taco', 'Slaw')

    expect(answer('Fish taco', 'Not reviewed')).toBeChecked()
    expect(answer('Slaw', 'Not reviewed')).toBeChecked()
    expect(screen.getByText('0 of 2 reviewed')).toBeInTheDocument()
  })

  it('sends an item nobody touched as not reviewed', async () => {
    const backend = installBackend({ items: [] })
    const user = userEvent.setup()
    await onAllergens(user, 'Fish taco')

    expect((await goLive(user, backend))[0].allergens).toBeNull()
  })

  it('leaves an item not reviewed when the list was opened and nothing ticked', async () => {
    const backend = installBackend({ items: [] })
    const user = userEvent.setup()
    await onAllergens(user, 'Fish taco')

    await user.click(answer('Fish taco', 'Has allergens'))
    expect(screen.getByText('Tick at least one, or it stays not reviewed.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Done with allergens in Fish taco' }))

    expect(answer('Fish taco', 'Not reviewed')).toBeChecked()
    expect((await goLive(user, backend))[0].allergens).toBeNull()
  })

  it('goes back to not reviewed when the only tick is taken off', async () => {
    const backend = installBackend({ items: [] })
    const user = userEvent.setup()
    await onAllergens(user, 'Fish taco')

    await user.click(answer('Fish taco', 'Has allergens'))
    await user.click(chip('Fish taco', 'contains', 'Milk'))
    await user.click(chip('Fish taco', 'contains', 'Milk'))
    await user.click(screen.getByRole('button', { name: 'Done with allergens in Fish taco' }))

    expect(screen.getByText('0 of 1 reviewed')).toBeInTheDocument()
    expect((await goLive(user, backend))[0].allergens).toBeNull()
  })

  it('takes none of these as a choice of its own, per item', async () => {
    const backend = installBackend({ items: [] })
    const user = userEvent.setup()
    await onAllergens(user, 'Fish taco', 'Slaw')

    await user.click(answer('Slaw', 'None of these'))

    expect(screen.queryByRole('button', { name: /^All/ })).not.toBeInTheDocument()
    const items = await goLive(user, backend)
    expect(items.map((item) => item.allergens)).toEqual([null, { none_of_these: true }])
  })

  it('picks allergens from the twelve, each at one level', async () => {
    const backend = installBackend({ items: [] })
    const user = userEvent.setup()
    await onAllergens(user, 'Fish taco')

    await user.click(answer('Fish taco', 'Has allergens'))
    expect(within(screen.getByRole('group', { name: 'Fish taco: contains' })).getAllByRole('button')).toHaveLength(12)
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    await user.click(chip('Fish taco', 'contains', 'Fish'))
    await user.click(chip('Fish taco', 'contains', 'Sesame'))
    await user.click(chip('Fish taco', 'may contain', 'Sesame'))
    expect(chip('Fish taco', 'contains', 'Sesame')).toHaveAttribute('aria-pressed', 'false')
    await user.type(screen.getByRole('textbox', { name: 'Why Fish taco may contain sesame' }), 'Shared with the buns')
    await user.click(screen.getByRole('button', { name: 'Done with allergens in Fish taco' }))

    expect(screen.getByText('Contains fish. May contain sesame.')).toBeInTheDocument()
    expect((await goLive(user, backend))[0].allergens).toEqual({
      none_of_these: false,
      contains: ['fish'],
      may_contain: [{ allergen: 'sesame', because: 'Shared with the buns' }],
    })
  })

  it('won’t go on from a may contain with no reason', async () => {
    installBackend({ items: [] })
    const user = userEvent.setup()
    await onAllergens(user, 'Fish taco')

    await user.click(answer('Fish taco', 'Has allergens'))
    await user.click(chip('Fish taco', 'may contain', 'Sesame'))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Say why Fish taco may contain sesame.')
    expect(screen.getByRole('heading', { name: 'Allergens' })).toBeInTheDocument()
  })

  it('keeps a review when the item is edited on the menu step', async () => {
    const backend = installBackend({ items: [] })
    const user = userEvent.setup()
    await onAllergens(user, 'Fish taco')
    await user.click(answer('Fish taco', 'None of these'))

    await user.click(step('Menu'))
    await user.click(await screen.findByRole('button', { name: 'Edit Fish taco' }))
    await user.clear(screen.getByRole('textbox', { name: 'Price' }))
    await user.type(screen.getByRole('textbox', { name: 'Price' }), '6{Enter}')

    const [item] = await goLive(user, backend)
    expect(item).toMatchObject({ base_price_cents: 600, allergens: { none_of_these: true } })
  })

  it('counts what is left at Go live, and says so when it is done', async () => {
    const backend = installBackend({ items: [] })
    const user = userEvent.setup()
    await onAllergens(user, 'Fish taco', 'Slaw')
    await user.click(answer('Slaw', 'None of these'))

    await user.click(step('Go live'))
    const part = await screen.findByRole('region', { name: 'Allergens' })
    expect(part).toHaveTextContent('1 of 2 reviewed')
    expect(part).toHaveTextContent('hands allergen questions about the others to a person')

    await user.click(screen.getByRole('button', { name: 'Go live' }))
    await waitFor(() => expect(backend.sent()).toHaveLength(1))
    expect(await screen.findByText(/1 item isn’t reviewed for allergens/)).toBeInTheDocument()
  })
})
