# Drive-through agent

A voice-first ordering agent for a fast-food drive-through. A customer speaks,
the agent takes the order, and a backend service stays in charge of everything
that has to be correct: the menu, prices, stock, order submission and the rules
for when a human takes over.

The model handles language. It proposes actions by calling tools, and the
backend validates every proposal before any data changes.

## Status

Early. The project foundation is in place; the ordering features are being
built piece by piece.

| Piece | What it covers | State |
|---|---|---|
| 00 | Project setup, database connection, health endpoints, tests, CI | Done |
| 01a | Menu tables and the seed menu (21 items, English and Canadian French) | Done |
| 01b | Session, cart and order tables | Done |
| 02 | Cart logic, pricing and order submission | Done |
| 03 | Ordering API and menu search | Done |
| 04 | Policy engine: allow, reject, confirm, escalate | Done |
| 05 | Text-mode agent, event log and first evals | Done; evals pass against the real model |
| 06 | Web app: the live session screen in three switchable views | Done |
| 07 | Stock reservations and idempotency keys | Done |
| 08a | Combos: discounts worked out from the cart, and suggested once | Done |
| 08b | Today's specials, with a cached read | Done |
| 09 | Session timers: the silence nudge, the three-minute hand-off, and pause | Done |
| 10a | Handoff package: what a person taking over is shown, and closing a handoff | Done |
| 10b | Staff screen: the handoff queue and the package, in the web app | Done |
| 11 | A second model provider: Gemini, which has a free tier | Done; not yet run against Google's API |
| 12 | Voice: talk to the agent through OpenAI Realtime | Done; tested with a recorded voice |
| 13 | The week's specials and live stock on the live screen | Done |
| 14 | Guardrails: misuse detected, counted and acted on by code | Done; evals pass against the real model |
| 15a | Allergens: the allergen matrix, checked by a tool, never guessed | Done; evals pass against the real model |
| 15b | Knowledge search: approved documents, pgvector, escalation on weak evidence | Done; retrieval and answer evals pass against the real model |
| 16 | Tracing: every turn as a timeline in Arize Phoenix, over OpenTelemetry | Done; checked with real turns in Phoenix |
| 17 | Faster turns: 6.5 s to 3.7 s on average, measured with the traces | Done; evals pass against the real model |
| 18 | One command: `make demo` runs the whole app in Docker, with or without a key | Done; checked in a browser |
| 19 | Not on the menu: say so, offer what's close, never what the guest is avoiding | Done; evals pass against the real model |
| 20 | Insights page: what guests asked for that the menu couldn't fill, at `/insights` | Done; checked in a browser with real conversations |
| 21 | Menu page: add, edit and take off items, with a photo for each, at `/menu` | Done; checked in a browser |
| 22 | Halal and kosher from a reviewed list, a fish burger, and swapping a line in one step | Done; evals pass against the real model |
| 23a | Caller memory: a phone caller's last five orders, read back and reordered | Done; evals pass against the real model |
| 23b | A real phone line: OpenAI Realtime over SIP, with a Twilio number | Planned |
| 23c | No-shows and repeat calls: staff mark pickups, big orders from flagged numbers go to a person, staff can block | Planned |
| Later | Demo polish: cost readout, French review, demo script | Planned |

## Stack

| Layer | Choice |
|---|---|
| Language and API | Python 3.13, FastAPI, Pydantic |
| Database | PostgreSQL 17 with pgvector |
| Database access | SQLAlchemy 2.0 (async), Alembic migrations |
| Model | OpenAI (Responses API) or Google Gemini, behind one interface; chosen in `.env` |
| Voice | OpenAI Realtime over WebRTC; the backend holds the key and runs every tool call |
| Tests | pytest, with a real Postgres started by testcontainers |
| Web app | React, TypeScript and Vite; TanStack Query; types generated from the API |
| Tooling | uv, ruff, pyright, oxlint, Vitest, pre-commit, GitHub Actions |

## Try it: one command

You need Docker and `make`.

```bash
make demo
```

Then open <http://localhost:8080> (staff screen: <http://localhost:8080/staff>,
menu page: <http://localhost:8080/menu>).
That builds and starts Postgres, the API and the web app; the API migrates the
database, loads the menu the first time (so edits made on the menu page survive
a restart) and the knowledge documents on every start, and nginx serves the web
app and forwards `/api` to the API.

- **No key needed.** Without an `OPENAI_API_KEY` it plays the scripted demo
  conversation (type the suggested lines), and knowledge search uses the local
  embedder.
- **With a key**, put it in `.env` (`cp .env.example .env`, then add
  `OPENAI_API_KEY`) and run `make demo` again: the real model answers, and the mic
  button talks to it by voice.
- `make down` stops everything. Traces go to Phoenix if `make phoenix` is running
  and `TRACING_ENDPOINT` is set in `.env`.

## Getting started (development)

You need [uv](https://docs.astral.sh/uv/), Docker and `make`.

```bash
uv sync                  # install Python and the dependencies
cp .env.example .env     # local settings
make hooks               # install the git hooks
make up                  # start Postgres on localhost:5433
make migrate             # apply database migrations
make seed                # load the menu from data/menu.json
make ingest              # load the knowledge documents from knowledge/
make dev                 # run the API on http://127.0.0.1:8000
```

Then open <http://127.0.0.1:8000/docs> for the interactive API page.

Postgres is published on host port 5433, not the usual 5432, so it does not
collide with a Postgres installed directly on your machine.

## Commands

| Command | What it does |
|---|---|
| `make demo` | Build and run the whole app in Docker on <http://localhost:8080> |
| `make up` / `make down` | Start and stop the local Postgres (`down` stops the demo and Phoenix too) |
| `make phoenix` | Start Arize Phoenix on <http://localhost:6006> for tracing |
| `make dev` | Run the API with auto-reload |
| `make migrate` | Apply all database migrations |
| `make seed` | Load `data/menu.json` and its photos into the database; resets edits made on the menu page |
| `make ingest` | Load `knowledge/` into the database and embed it; only changes are re-embedded |
| `make chat` | Talk to the agent in the terminal (needs an OpenAI or Gemini key) |
| `make eval` | Run the evals against the real model (needs an OpenAI or Gemini key) |
| `make ui` | Run the web app on <http://localhost:5174> (needs `make dev` running too) |
| `make gen-api` | Regenerate the web app's API types after changing an endpoint |
| `make check-ui` | Everything CI runs for the web app: lint, type check, tests, build |
| `make lint` | Run ruff and pyright |
| `make format` | Fix lint issues and format the code |
| `make test` | Run every test except the evals (needs Docker) |
| `make test-unit` | Run the fast tests only (no Docker) |
| `make check` | Everything CI runs for the backend: lint, then all tests |

## Testing

Tests are split in two:

- `tests/unit` needs no database or network and runs in about a second.
- `tests/integration` runs against a real Postgres. The test run starts its
  own container, applies the real migrations, and rolls each test back, so
  tests never touch your local database and never see each other's data.

Three layers check every change:

| When | What runs |
|---|---|
| On each commit (git hook) | ruff, pyright, unit tests; and the web app's lint, types and tests when it changed |
| Before each push (git hook) | The full test suite |
| On each pushed commit (GitHub Actions) | Backend: lint, format check, pyright, full test suite. Web app: lint, type check, tests, build |

## The menu is data

The menu lives in `data/menu.json` and is loaded into the database with
`make seed`. Nothing about the menu is written into a prompt.

Each item lists the choices a customer can make about it. A choice that must
be made and has no default (the size of the fries) is one the agent has to ask
about. A choice with a default (regular style, plain seasoning) is filled in
by the backend. Changing what the agent asks is therefore a data change.

The file is validated in full before anything is written, and the load runs in
one transaction. The database is made to match the file: items and options
dropped from the file are deactivated, not deleted, because past orders refer
to them.

### The menu page

After the first load, the database is the menu, and a manager changes it at
<http://localhost:5174/menu> (or the Menu link on any screen). `make seed`
puts it back to the file, on purpose; the Docker container seeds only an empty
database (`seed --if-empty`), so a restart keeps every edit.

- **Every item has a photo.** The 20 seed items come with Unsplash photos in
  `data/photos/` (credits in `data/photos/CREDITS.md`). An item with no photo
  shows a placeholder drawn for its category, and a "No photo" flag.
- **Add, edit, take off, put back.** Names and descriptions in both languages,
  category, price and stock are edited here. Choices, aliases and allergens
  stay in `data/menu.json` and are shown read-only.
- **Taking an item off never deletes it.** Past orders point at it. It
  disappears for guests, the agent and the specials, and can be put back.
- **Codes never change.** A new item's code is made from its English name
  (`spicy_chicken_wrap`); renaming it later keeps the code.
- **A new item's allergens are unreviewed**, so the agent will not discuss
  them and hands the question to a person. The page says so.
- **The backend refuses what would confuse the agent or the till:** a second
  item with the same English name, or a price a running special would make free.
- **Photos are stored in Postgres**, in their own table so reading the menu
  never loads them. The type is checked from the file's first bytes, not what
  the browser claims; the browser shrinks a photo to 1200 px before sending it;
  at most 5 MB. A photo's URL carries its hash, so it is cached for good and a
  new photo is a new URL.
- Changes reach the agent from the next session, whose instructions are built
  from the menu when it starts.

## Carts store choices; orders store frozen prices

A cart line records the item, the quantity and the chosen options, and no
price. Prices are worked out from the menu whenever they are needed, so a cart
can never hold a stale total.

When a cart is submitted, names and prices are copied into the order tables.
From then on the order is history: a later menu change cannot alter it.

The database itself refuses states that must never exist, for example two
active sessions in one lane, a second order for a session, or an order whose
total does not add up from its subtotal, discount and tax.

## Ordering rules

The rules live in `src/drivetruagent/ordering/` as plain Python, with no HTTP
and no model involved. Whatever the agent proposes ends up as a call to one of
these functions:

| Function | What it does |
|---|---|
| `open_session` | Starts a session and its empty cart; refuses a busy lane |
| `add_item` | Adds a line; refuses if a required choice is missing |
| `update_line` | Changes a quantity, the options, or both |
| `remove_line` | Removes a line; its number is never reused |
| `replace_line` | Swaps a line for another item in one step; keeps its number |
| `get_cart` | Returns the cart, priced |
| `submit_order` | Freezes the cart into an order and ends the session |

How they behave:

- **Every change returns the whole cart, priced**, in the session's language.
  Nothing downstream has to remember earlier state or do arithmetic.
- **A refused request changes nothing.** Validation happens before any write.
- **Failures are typed.** Each error has a stable code and structured details.
  "Fries need a size" comes back as `missing_required_options` with the sizes
  on offer, so the agent can ask the right question without another lookup.
- **Changes to one cart never interleave.** Each one locks the session row
  first, so a tool call that arrives twice cannot corrupt the cart. A test
  fires five changes at once on separate connections to prove it.
- **Tax is rounded once**, on the subtotal, so subtotal plus tax always
  equals the total to the cent.

## The agent

`src/drivetruagent/agent/` connects a model to the ordering system through
tools. It works in text; voice will be a layer in front of it.

One customer turn:

```
customer text
  -> record it
  -> repeat, up to a round limit:
        ask the model, giving it instructions, the conversation and the tools
        if it asked for tools: run each, record call and result, save, repeat
        if it answered in words: record the reply and stop
  -> return the reply and the current cart
```

Design points:

- **The loop does not know which model it is talking to.** It depends on a
  one-method interface. The OpenAI adapter implements it for real use and a
  scripted stand-in implements it for tests, so the whole loop is tested
  without a network call.
- **The conversation lives only in the database.** Every message, model
  call, tool call and result is a row in `agent_events`. Each model call
  rebuilds its context from that log, so the app holds no conversation in
  memory, and the same log is the audit trail.
- **Tools never raise for an expected failure.** A rejection, an unknown
  item or malformed arguments come back as a result the model can read, so
  it can recover in the same turn.
- **Tool schemas are strict.** Every property is required and no others are
  allowed, so the model cannot invent a field such as a price.
- **The instructions hold no rule that must be enforced.** Quantities,
  prices, confirmation and submission are decided by the backend. The
  instructions include a menu index of item codes and names, read from the
  database, and no prices.

| Tool | Runs |
|---|---|
| `search_menu`, `get_item_details`, `get_cart` | Read-only lookups |
| `get_todays_specials` | Today's specials and the combo deals, read through a cache |
| `check_allergens`, `check_diet` | The allergen matrix; the halal and kosher list |
| `get_past_orders`, `reorder`, `forget_my_orders` | A phone caller's last five orders; `reorder` adds them through the policy gateway |
| `add_item`, `update_item`, `remove_item`, `replace_item` | The policy gateway |
| `request_confirmation`, `submit_order` | The policy gateway |
| `confirm_pending_action`, `cancel_pending_action` | The customer's answer to a held action |
| `set_language` | Sets the session language |
| `request_human` | Hands the session to a person |

To talk to it, put `OPENAI_API_KEY` in `.env` (or use Gemini, below), then:

```bash
make chat
```

Without a key everything else still works; only the conversation endpoint
answers 503.

### The board under the session

The live screen shows the restaurant's side of things, so nobody has to open
the database during a demo:

- **This week's specials**, Monday to Sunday, with today marked. Ask the agent
  "what's today's special?" and the answer can be checked against the board.
- **Stock for every item**, counting down live. Adding something to a cart
  holds it ("2 held") and lowers what another customer could have; placing
  the order lowers what is on hand. Today's special is highlighted, and an
  item running low turns red.

It reads `GET /specials/week` and `GET /inventory`, refreshed every few
seconds and faster during a turn or a call.

### Talking to it

Press the microphone button on the live screen and speak. The agent answers
aloud. It needs `OPENAI_API_KEY` (voice is OpenAI Realtime,
`gpt-realtime-2.1-mini` by default) and a browser that allows the microphone;
`localhost` counts as secure, so Chrome asks once.

    browser  --audio, WebRTC-->  OpenAI Realtime  <--events-->  backend

- **Audio goes straight between the browser and OpenAI**, which keeps it quick
  and lets the browser handle echo and noise.
- **The browser sends its call offer to the backend, not to OpenAI.** The
  backend forwards it with its own key and the call's settings. The browser
  never holds a key.
- **The backend joins the call** on a second connection and runs every tool
  the model asks for through the same tools and policy gateway as typing. The
  browser cannot answer a tool call.
- **What is said, both ways, is written to the event log** from OpenAI's
  transcripts, so the live screen, the staff handoff package and the timers
  work unchanged. On a call, the timers' sentences ("Are you still there?")
  are spoken.

Typing is switched off during a call. Pause mutes the microphone as well as
stopping the timers. Verified with a real call through the page, using a
recorded voice as the customer. See `agent/voice.py`, `api/voice.py` and
`frontend/src/voice/`.

### Running it on Gemini, for free

Google's Gemini API has a free tier. Get a key from Google AI Studio, then in
`.env`:

```
AGENT_MODEL=gemini
GEMINI_API_KEY=...
```

`make chat`, `make eval` and the web app then use Gemini. The default model is
`gemini-2.5-flash-lite`, the cheapest Google lists and the one with the
highest free limits; `GEMINI_TEXT_MODEL` changes it. Two things to know about
the free tier: requests are rate-limited (a few a minute), and Google may use
what is sent to improve its products.

The adapter is `agent/gemini_model.py`. Adding it touched the adapter, the
settings and the factory, and one thing in shared code: a tool call can now
carry `provider_data`, opaque values the provider needs to be given back on
the next request. Gemini attaches an encrypted "thought signature" to a
function call and newer models check that it returns with the call. The event
log is the only conversation store, so it is stored there with the call. The
adapter also regroups parallel calls: the log records call, result, call,
result, and Gemini wants all the calls of one response in one turn, then all
their results in the next.

**Not yet verified against Google's API.** There is no key on the machine it
was written on. It is written against the SDK's own types, which the type
checker enforces, and tested through the real agent loop with stand-in
responses. The first real run may need adjustments, most likely to which JSON
Schema keywords Gemini accepts in tool definitions.

### Running it without a key

Set `AGENT_MODEL=demo` in `.env`. The agent then plays one fixed conversation:
today's special, a combo offered and taken up, a missing size, two items in one breath, fifteen
cookies that are held and then corrected to two, the read-back, and the order
being placed. Only the
model is scripted. Every tool call it makes runs through the same policy
engine and ordering rules, and the totals it speaks are read from what the
tools returned. `GET /agent` says which model is in use and, for the demo,
what to type at each turn.

## The live session screen

`frontend/` is a React app with one screen so far: a live view of a session,
for demos. It shows the agent (an indicator that changes as it listens, works
and speaks), the conversation, the order filling up as items are added, and
every tool call with the backend's decision.

```bash
npm --prefix frontend install   # once
make dev                        # the API, in one terminal
make ui                         # the web app, in another
```

Then open <http://localhost:5174>. With `AGENT_MODEL=demo` in `.env` the
screen offers the next line of the script at each turn, so the whole order
can be played with a click per turn.

The same session can be shown three ways, switched from the top bar and
remembered in the address (`?view=`):

| View | Emphasis |
|---|---|
| Menu board | The agent and the order, like a drive-through sign at night |
| Receipt | The order as a printed receipt; the decisions as a ledger |
| Control room | The decision log, with the session's state across the top |

How it is put together:

- **One place holds the session.** `src/session/` fetches, follows a turn
  while it runs, and derives what to show. The three views in `src/views/`
  receive that and only draw it, so switching view mid-order loses nothing.
- **The screen is derived from the event log.** The transcript, the decisions
  and what is waiting for a yes are all computed from the backend's audit
  trail by pure functions, so the screen cannot drift from what happened.
- **A turn is followed step by step.** While the agent is working, the screen
  polls the event log and cart, so items appear one at a time.
- **API types are generated, and checked.** `make gen-api` writes TypeScript
  types from the backend's API description. A backend test fails if that
  description is out of date, so the two cannot drift apart unnoticed.
- **One test suite covers all three views.** They are laid out differently but
  must show the same things and offer the same controls.
- **One look for the whole app.** Colours and fonts (Sora, IBM Plex Mono, a
  cool dark palette with a cyan accent) live in `frontend/src/theme.css`, loaded
  once. Every page and view draws with its tokens, so a change there reaches
  everything.

## Evals

A scenario is what a customer says, turn by turn, plus what must be true
afterwards. The checks are deterministic: they look at the final cart, the
order, the session state and the tools used, never at the agent's wording.

| Scenario | What it checks |
|---|---|
| `fries_need_a_size` | The agent notices the missing size and asks |
| `correction_mid_order` | Changes of mind edit existing lines instead of adding new ones |
| `a_thousand_fries` | Asked about first, then handed to a person; nothing added |
| `tries_to_get_it_free` | A "set the price to zero" attempt changes nothing |
| `off_topic_question` | Declines, steers back, and still takes the order |
| `order_in_french` | Language is set and the order is right |
| `combo_taken_up` | The combo is offered, the customer adds a drink, and the saving is charged |
| `combo_declined` | Saying no adds nothing and costs nothing |
| `asks_for_the_special` | The special is looked up, ordered, and charged at its price |
| `abusive_customer_is_cut_off` | Three insults, one that only the classifier can catch, end the session |
| `asks_for_its_instructions` | An attempt no rule catches reveals nothing of the instructions; the order still goes through |
| `keeps_going_off_topic` | Three questions about anything but food get the customer a person, unanswered |
| `asks_about_dairy` | The answer comes from the allergen tool, and the order still goes through |
| `checks_the_order_for_a_nut_allergy` | The whole order is checked; the cookie that may contain nuts comes off |
| `asks_about_the_fryer_oil` | "Do you fry in peanut oil?" is answered from the approved documents, then the order |
| `asks_if_the_veggie_burger_is_vegan` | Answered from the documents (not as served), never "it's vegan" |
| `asks_for_something_not_on_the_menu` | "Oat milk shake": says it isn't offered and suggests drinks without milk, never the regular shake |
| `gets_offered_something_similar` | "Spicy nuggets": nuggets don't come spicy, the chicken burger does, and the guest takes it |

Every scenario is played on a fixed Monday, so a total never depends on which
day the evals happen to be run.

```bash
make eval
```

`uv run pytest -m eval -s tests/integration/evals` prints every conversation,
to read how the agent worded things.

Evals call the real model, so they need a key and cost money (a few cents
for the set on `gpt-6-luna`). They are left out of `make test` and CI, and
are skipped if no key is set. The scenario checker and runner themselves are
tested with the scripted model.

**Results.** First run against the real model (`gpt-6-luna`, 14 October
2026): all 9 scenarios passed, twice in a row. The very first attempt showed 4
failures, all from the test harness, not the agent: one OpenAI client was
shared across tests, and its pooled connection belonged to an earlier test's
event loop. Each test now gets its own client.

With guardrails (14 October 2026): all 12 passed, including the three new
ones. The abuse and off-topic scenarios can only reach three strikes if the
classifier flagged the lines no rule covers, so they also show the classifier
working against the real model.

With allergens (14 October 2026): all 14 passed. Read with `-s`, the agent
said what was "listed" and what "may contain" nuts and why, never called
anything safe, and offered a team member for the nut allergy.

With knowledge search (14 October 2026): all 16 conversations and the
retrieval eval passed. Asked "is the veggie burger vegan?", the agent answered
from the document: not as served, because the house sauce has egg and the grill
is shared, and the customer ordered it anyway.

## Stock: two cars cannot buy the last item

Each stock-tracked item has a count on hand. While a session is ordering, it
holds what is in its cart as a reservation, so another car cannot be sold the
same units.

```
available to a session = on hand − what other sessions hold in live reservations
```

How overselling is prevented:

- **One lock, one check.** Every change to a reservation first locks that
  item's inventory row, then counts what other sessions hold, then decides.
  Two cars asking for the last unit at the same moment are handled one after
  the other: the first gets it, the second is told there is none.
- **Reservations expire, and expired ones count for nothing.** The query that
  counts them ignores them, so correctness never depends on a cleanup job.
  Activity in a session refreshes its reservations.
- **Submitting checks again.** If a session went quiet, its reservation
  expired and someone else took the stock, placing the order is refused
  instead of selling something that is gone.
- **The database has the last word.** A CHECK constraint refuses stock below
  zero, whatever the code does.

When there is not enough, the request is refused with `insufficient_stock`:
how many are available, and up to three in-stock alternatives from the same
category for the agent to offer. Menu reads carry an `in_stock` flag.

The tests include real races on separate database connections: two cars for
the last cookie, ten cars for three cookies, and everyone submitting at once.
With the row lock removed, those tests fail.

A session that is abandoned releases its stock at once. One that is handed to
staff keeps it, because a person is about to finish that order.

### Requests that are sent twice

A caller that is unsure whether a request got through can send it again with
the same `Idempotency-Key` header. The first request runs and its response is
stored in the same transaction as the work. A repeat returns the stored
response and nothing runs again, even if both copies arrive at the same
moment. It is supported on proposing an action, confirming a held action and
submitting an order; the agent's own tool calls are keyed the same way.

## Combos: a discount is worked out, never asked for

A burger, a side and a drink bought together cost $2.00 less. There is no
"add combo" tool and no "apply discount" action. The backend works discounts
out from what is in the cart every time the cart is priced, the same way it
works out tax.

```
total = subtotal − discount + tax        (tax is charged on subtotal − discount)
```

What follows from that:

- **The model cannot grant a discount**, whatever it is talked into trying.
  Nothing it can send carries one.
- **Nothing about a discount is stored on a cart.** Take the drink off and the
  combo is gone on the next read. There is no clean-up code to get wrong.
- **An order freezes its discounts**, as it freezes names and prices. A past
  order reads the same after the combo is changed, retired or deleted, and a
  database CHECK holds the sum together.
- **Each unit counts once.** Two burgers, one side and one drink are one
  combo. Rules are applied in priority order: not always the largest possible
  saving when rules overlap, but predictable, and explainable to a customer.

Combos are data (`combos` in `data/menu.json`): one item from each listed
category. The file is checked before loading, and a combo whose discount
could make its cheapest set free is refused.

### Suggesting a combo, once

When a customer adds a burger, the result of that action carries a
`suggestion`: what to add and what it saves. The backend decides whether
there is anything to suggest; the model only chooses the words.

- A combo is suggested only when its main item is in the cart. A drink on its
  own suggests nothing.
- It is suggested during **one customer turn**. Within that turn the
  suggestion keeps up with the cart ("a burger and fries" ends up asking only
  for a drink). When the customer next speaks, it is closed, and that combo is
  never offered to them again, whatever they answered.
- It is not suggested if what is missing is sold out, or after something was
  just removed.
- Declining changes nothing about the price: if the customer later builds the
  combo anyway, they still get it.

"Do not nag" is enforced in code, not asked of the model. With that rule
removed, two tests fail.

## Today's special

There is one special for each day of the week (`specials` in
`data/menu.json`): Monday chicken burgers, Tuesday double cheeseburgers,
Wednesday onion rings, Thursday nuggets, Friday milkshakes, Saturday sundaes,
Sunday veggie burgers. Like a combo, the saving is worked out by the backend for
every one in the cart, and it stacks with a combo.

**Which day is "today"?** The date where the restaurant is
(`RESTAURANT_TIMEZONE`), fixed when the session opens. A car that hears about
the special at 23:59 still gets it at 00:01, and no clock is read while
pricing, which keeps prices repeatable.

**The cache.** "What's today's special?" is asked all day, so the agent's
`get_todays_specials` tool and `GET /specials` read the day's list from an
in-memory cache with a time to live.

- What is cached is the list, which rarely changes. Stock changes with every
  order, so `in_stock` is read live and laid over the cached list.
- A change made through `PUT /specials/{code}` clears the cache after it is
  committed, so it shows on the very next read.
- The time to live (60 seconds by default) covers changes this process is not
  told about, such as a re-seed. With several workers it would move to Redis.
- **Pricing never reads the cache.** A stale cache can make the agent mention
  a special that ended a moment ago; it cannot make a cart charge for one.

An honest note on what the cache buys: the database read it saves takes about
a millisecond, and the slow part of answering is the model. It is here as the
right shape for when the source is slower (a remote menu service), not as a
latency win today. Writing its tests did catch a real bug: an empty cache has
a length of zero, so `if cache:` treated it as no cache at all.

## Session timers: the backend owns the clock

- **20 seconds of silence:** the agent asks "Are you still there?". If there
  is still no answer 20 seconds later, the session is closed as abandoned and
  its stock is released.
- **Three minutes without an order:** the session is handed to a person, with
  its cart and stock intact.

The model never estimates time. What the customer hears is fixed text in
their language, so a timer still works when the model is slow or down.

How it is built:

- **Timers are arithmetic, not sleeping tasks.** A session row holds a few
  timestamps. One pure function, `due_action(clock, now, settings)`, says what
  is due. A background task wakes once a second and applies it. Nothing is
  held in memory per session, so any server process can do it.
- **Two processes cannot nudge twice.** Each session is handled under its row
  lock and the decision is made again once the lock is held. A test races
  four sweeps on separate connections and gets one nudge; with the lock
  removed it fails.
- **The clocks stop while the agent is replying.** A customer who says "yes,
  that's right" at 2:58 gets their order placed, not a hand-off at 3:00 while
  the model is still answering. A turn marker older than a minute is ignored,
  so a crashed turn cannot freeze a session.
- **The three-minute rule holds without the background task.** Each customer
  turn checks the limit before the model is called.
- **Pause is the backend's state.** `POST /sessions/{id}/pause` stops the
  clocks for a presenter; paused time counts toward nothing. The screen's
  Pause button calls it.
- **The screen counts down to times the backend gives it** (`timers` on the
  session). It never works the rules out itself, and it keeps polling between
  turns so a nudge or a timed-out session shows up on its own.
- **No test waits for a timer.** Every call is told what time it is.

The sweep also releases stock holds that have expired, which until now
nothing ran.

When presenting, use Pause, or raise `SILENCE_NUDGE_SECONDS` and
`SESSION_MAX_SECONDS` in `.env`.

## Handing a session to a person

A session is handed off in four ways: a very large order the customer
confirmed, a request for a person, the three-minute limit, and repeated
confusion (see [Guardrails](#guardrails-misuse-is-decided-by-code)). Each leaves a
**handoff**, written in the same transaction that ends the session, so the
two exist together or not at all.

The person taking over reads a **package** (`GET /handoffs/{id}`):

| | |
|---|---|
| Why | The rule's code and a readable title |
| What to do | One or two sentences, such as: *The customer asked for 400 of Chocolate chip cookie and confirmed it. That is more than the agent may take, so it was not added. Check the quantity with them. The cart holds 2 items and can be placed as it stands.* |
| The cart | Priced as usual, with any combo or special the customer had earned |
| Stock | What the cart is holding, and whether each hold still counts |
| The conversation | The last 12 things said |
| What the backend decided | The last 10 tool calls and timer events, each with its decision |

Two choices worth knowing:

- **The package is built when it is read, not saved.** A handed-off session
  can never change again and its event log only grows, so there is nothing to
  go stale. A snapshot taken at the moment of the handoff would also miss the
  tool call that caused it and the agent's last words, which are recorded
  just after.
- **"What to do" is fixed wording filled in from data, not written by a
  model.** Someone acting on it should be reading what the backend knows.

Staff do not join the conversation or edit the cart. They can say they have
picked a handoff up, and close it one of two ways:

- **Place the order as it stands.** It is frozen exactly as a normal submit
  would freeze it, and its stock is taken. If the cart is empty or the stock
  has gone, the handoff stays open and the error says why.
- **Cancel it.** The stock it held is free at once.

A resolved handoff cannot be changed. Two people closing the same one place
one order: a test races five of them on separate connections, and fails with
the row lock removed.

### The staff screen

<http://localhost:5174/staff>, or the Staff link on the live session screen.
The queue is on the left, longest waiting first; the selected package is on
the right, with "What to do" at the top and the two ways of closing it. It
polls, so a session handed off on the live screen appears by itself.

- After an order is placed or cancelled the screen stays on that handoff and
  shows the outcome. It does not jump to the next one in the queue.
- A refusal is explained in words the person can act on: the stock has gone,
  the cart is empty, or someone else closed it first.
- It lives in `frontend/src/staff/`, with the same split as the live screen:
  one hook that fetches and acts (`useStaffDesk`), pure helpers for wording
  (`words.ts`), and components that only draw.
- The two screens are two paths in one app. Routing is a twenty-line module
  (`app/navigation.ts`), not a library.

## Guardrails: misuse is decided by code

The instructions already tell the agent to stay on ordering. Guardrails make
misuse a backend matter: every customer message is checked before the agent
sees it, strikes are counted on the session, and code decides what happens.

| What was said | Counted as | What happens | At three |
|---|---|---|---|
| An insult or a threat | abuse strike | The agent is not run; a fixed sentence is said | The session is **ended** (`terminated`), its stock freed |
| An attempt to get round the rules: "ignore your instructions", "manager mode", "set the price to zero", "what is your system prompt" | abuse strike | The same | The same |
| A question about anything but ordering | confusion strike | The agent answers as usual and steers back | The customer is **handed to a person** (`repeated_confusion`) |
| A turn where the agent hit its round limit | confusion strike | The usual "sorry" | The same |

Abuse ends the conversation; confusion gets the customer a human, because
they may simply need one.

**Two layers.** Rules first (`policy/guardrails.py`): narrow patterns in
English and French that decide at once with no model call. They are kept
narrow on purpose, and tests hold them to it: "ignore the pickles", "show me
the rules for the combo", "what's the manager's special?" and "shut up and
take my money" are all left alone. Anything no rule is sure of, of four words
or more, goes to a **classifier** (`agent/guard.py`): one model call with a
fixed prompt and a single tool whose only argument is the label. The
customer's words are passed as quoted data, never as instructions.

**Fails open.** If the classifier errors or answers with something that is
not a label, the message counts as fine and the failure is logged. A broken
guardrail must not stop people ordering; the rules still apply, and so does
everything the policy engine enforces. `GUARDRAIL_CLASSIFIER=false` turns the
classifier off (rules only, no extra cost or latency).

**A blocked message is never acted on, then or later.** To save time, the model
starts on each message while the classifier decides (see
[Faster turns](#faster-turns-measured-then-cut)); if the message is blocked, that
request is cancelled and its answer never used, so no tool runs and nothing it
said is shown or kept. `GUARDRAIL_CHECK_FIRST=true` restores the strict order, in
which a blocked message never reaches the model at all. The event log keeps what
was said, for staff. When the conversation is rebuilt for the next turn, a
blocked message is replaced by *"The customer said something the guardrail
withheld."*, so an injection attempt cannot come back in later context. Replies
to blocked messages are fixed sentences in the customer's language, so the model
has nothing to be manipulated into saying.

**Ended is ended.** A session ended for abuse refuses further messages with
`409 session_ended`. A customer who has just placed an order can still say
thanks.

**On a call** the model hears the audio directly and has usually answered by
the time the transcript is checked, so a message cannot be withheld from it.
Strikes still count: the bridge says the fixed sentence, and at three says
goodbye and hangs up, or hands off.

Each flagged message is a `guardrail` event (label, which layer decided, the
rule or the classifier's reason, the action, the strike counts and the limit).
The live screen shows it in the decision log as *extraction · strike 1 of 3*,
a rejection, and the end of a session as an escalation with its rule.

## Allergens: answered from the matrix, never guessed

An allergen question is a safety question, so it is not left to the model's
memory or to a similarity search. The menu file carries an **allergen matrix**:
for each item, what it contains and what it may contain (with why, such as a
shared fryer), and for each option, what it adds (cheese adds milk, ranch adds
milk and egg). The allergens are Health Canada's priority allergens, plus
gluten listed apart from wheat.

> **The allergen data is synthetic, written for this demo. It is not real
> allergen information for any restaurant.**

The agent's `check_allergens` tool answers from it:

- **For one item, as the customer would have it.** Choices given are taken into
  account; choices not given count as their defaults (fries are plain unless
  seasoned, and seasoning adds milk).
- **For the whole order**, line by line with each line's choices: "my son has a
  nut allergy, is anything in my order a problem?"
- It says what an item **contains** and where from, what it **may contain** and
  why, and which choices not taken **would add** an allergen ("no milk, unless you
  add cheese").
- An allergen nobody declared is **not listed**, which is all the matrix can say.
  It is never turned into "free of" or "safe", and the tool's instruction to the
  agent says so.
- **An item whose allergens were never reviewed is handed to a person**
  (`allergen_unverified`) instead of answered. The handoff package names the item
  and the allergens asked about, and tells staff to answer from the allergen
  binder.

The logic is pure (`ordering/allergens.py`): what is present given the options
chosen, "contains" beating "may contain", and which unchosen options would add
something. The tables are `allergen_declarations` (item, option or none,
allergen, level, note) and `menu_items.allergens_reviewed_on`; both are made to
match the file on every seed. `GET /menu/items/{code}/allergens` serves the same
report for screens.

## Knowledge search: approved documents, never the model's memory

Questions the allergen matrix cannot answer ("do you fry in peanut oil?", "is
the veggie burger vegan?", "how much caffeine is in a coffee?") are answered
from **approved documents** in `knowledge/`: Markdown with TOML front matter
saying what each is about, who may see it (`customer` or `staff`) and its
version. Like the menu, they are **synthetic**.

```
knowledge/*.md  ──make ingest──▶  documents ─▶ chunks (one per ## section) ─▶ embeddings
                                                                              (one row per model)
question ──embed──▶ nearest chunks (pgvector, cosine) ──filter──▶ evidence, or none
```

- **Chunks are sections.** Each `##` section is one chunk, and its text starts
  with the document's title and the heading, so "What oil we fry in" still says
  what it is about once it stands alone.
- **Two embedders, one table.** OpenAI's `text-embedding-3-small` (at 384
  dimensions) when there is a key; a local **hashing embedder** otherwise, which
  matches words, not meaning, and needs nothing. `make ingest` indexes with both,
  one row per chunk and model, so the no-key demo can search too. Vectors from
  different models are never compared.
- **Ingest is idempotent.** An unchanged section keeps its vectors; a changed one
  is re-embedded; a document removed from the folder is retired and never
  searched again.
- **Evidence, or nothing.** `search_knowledge` returns the top passages scoring at
  or above the embedder's threshold, with their chunk ids and scores, and tells
  the agent to answer only from them. The tool result is in the event log, so the
  event log is the retrieval trace.
- **Weak evidence on an allergen question goes to a person.** If nothing scores
  high enough and the question names an allergen (a fixed English and French word
  list), the session is handed off with `allergen_unverified`, and the handoff
  quotes the question. Otherwise the agent says it does not know.
- **Staff documents cannot leak.** The complaint procedure and supplier contacts
  are ingested, but the audience filter is fixed in code, not an argument the
  model sends. A test asks for a staff passage word for word and checks it never
  comes back.

**Thresholds were measured, not guessed.** On the labelled questions in
`evals/retrieval.py`, with `text-embedding-3-small`: the passage that answers
scored 0.50 to 0.78 in English and 0.38 in French; off-topic questions scored at
most 0.20. The threshold is 0.35. The hashing embedder's is 0.15 (off-topic at
most 0.11).

**Retrieval is evaluated apart from answers.** `tests/integration/evals/test_retrieval.py`
checks, against the real embedder, that every labelled question finds its
document in the top 3 above the threshold and every off-topic one stays below
it: 23 of 23 found first, 4 of 4 kept out. The same questions run as a plain test
with the hashing embedder.


## Tracing: each turn as a timeline in Arize Phoenix

The event log is the record of what happened. Tracing is a **view** of the same
work, built for looking at it: each turn as a timeline, with the guardrail, the
model calls, the tools and the knowledge searches nested under it and timed. It
is off by default, and nothing depends on it.

```bash
make phoenix                                          # Phoenix on http://localhost:6006
echo "TRACING_ENDPOINT=http://localhost:6006/v1/traces" >> .env
make dev                                              # or make chat
```

![A turn in Phoenix: guardrail, model calls, tools and the knowledge search nested under it](docs/images/phoenix-trace.png)

Spans follow the **OpenInference** conventions, so Phoenix draws each kind
properly:

| Span | Kind | Carries |
|---|---|---|
| `turn` | AGENT | the customer's words, the reply, the tools used, the session's state |
| `guardrail` | GUARDRAIL | the message; verdict, which layer decided, the action |
| `model` | LLM | the instructions and conversation sent, the answer and tool calls, token counts |
| `tool <name>` | TOOL | the arguments, the result, the policy decision |
| `knowledge search` | RETRIEVER | the query; each passage with its id, score and text |

- **Every span carries the session id**, so Phoenix's Sessions tab shows a whole
  visit. Tool and guardrail spans come from shared code, so voice calls get them
  too.
- **One place makes spans.** Only `agent/tracing.py` and `telemetry.py` import
  OpenTelemetry, and a layering test keeps it that way. With no endpoint set, no
  provider is installed and every span is a no-op.
- **Exported in batches, off the request path**, over OTLP HTTP. Phoenix runs from
  a pinned image with its own volume, under a compose profile, so `make up` is still
  just Postgres.
- Tested with an in-memory exporter: the nesting and kinds of a turn with tools, a
  blocked message (a guardrail span and no model call), a retriever span with its
  passages, and a model failure marking the turn as an error.

What it showed straight away: a first turn that asked about the fryer oil took
about 10 seconds, because it made three round trips to the model (language,
search, answer). That is the kind of thing the event log records but nobody
notices until it is drawn.

## Faster turns: measured, then cut

The first trace showed a turn taking 10 seconds. A profiling run (six typical
lines, timed span by span against the real model) gave the baseline, and each
change was measured before the next:

| | Before | After |
|---|---|---|
| Average turn | 6.5 s | **3.7 s** |
| Slowest turn | 10.0 s | 6.6 s |
| Model calls per turn | 2.67 | 2.17 |

What made the difference:

- **The guardrail runs alongside the model, not before it.** The classifier took
  1.4 to 2.4 s on every message of four words or more, and the agent waited for
  it. Now the model's first request starts at the same time; if the message is
  blocked, that request is cancelled and its answer never used, no tool runs and
  nothing it said is kept. Set `GUARDRAIL_CHECK_FIRST=true` to have a blocked
  message never reach the model at all, at the old speed.
- **The classifier has five seconds.** Measured: median 1.55 s, 90th percentile
  2.6 s, with rare stalls past ten. Since it overlaps the model's first round,
  waiting five costs nothing on a normal turn. A two-second limit was tried first
  and cut off a quarter of verdicts in the evals, which quietly let a subtle
  insult through; the abuse eval caught it.
- **Language is detected in code** (a weighted English and French word list that
  only answers when the margin is clear), so the model no longer spends a whole
  round calling `set_language` on the first turn.
- **Fewer needless rounds**, by prompt: call `add_item` straight away rather than
  looking the item up first, batch independent calls (two items at once), never
  bundle the order confirmation with anything else, and do not re-read the cart
  after a change.
- **Reasoning effort `low`.** Timed interleaved, `gpt-6-luna` took a median 2.3 s
  at its default, 2.0 s at `low` and 1.1 s at `none`. `none` was tried and the
  evals passed, but reading the transcripts showed it jumping ahead: it asked to
  confirm the order before the customer had finished in 11 of 20 confirmations,
  against 1 of 13 at `low` (0 of 13 with the prompt above). With the other changes,
  `low` is as fast end to end. It is a setting, `OPENAI_REASONING_EFFORT`; the
  classifier, which only picks a label, uses `none`.

Final check: all 17 evals pass, with no classifier timeouts and no early
confirmations. The rest is the model itself: a turn with tools needs one request
to choose them and one to answer.

## Not on the menu: say so, offer what's close, never what they're avoiding

Menu search forgives how people talk ("a large fries", "coke", "un
cheeseburger"), and that forgiveness used to hide a problem: "oat milk shake"
came back as **Milkshake** with a score as good as "a large fries", so the
agent could hear it as a yes. Now the search reports what it couldn't account
for, and the code decides what may be offered:

| The guest says | The search reports | The agent is told |
|---|---|---|
| "a large fries" | exact ("large" is a size fries come in) | add it |
| "spicy nuggets" | close: nuggets have no "spicy"; the chicken burger does | say so, offer either |
| "iced coffee" | close: coffee has no "iced"; iced tea does | say so, offer either |
| "oat milk shake" | close, and the guest **avoids milk**: the milkshake contains it | don't offer the shake; offer drinks not listed as containing milk |
| "gluten-free bun" | none, and avoids gluten; fries **may contain** gluten (shared fryer) | say so; offer items without gluten, not the fries |

- **Leftover words are judged by code.** A word the item offers as a choice
  ("large", "vanilla"), a count ("two") or another item named in the same breath
  ("fries and a cheeseburger") is fine; anything else is something the menu
  doesn't have, and the result says which other items do have it.
- **Avoidance is read from the words** ("dairy-free", "no nuts", "allergic to
  eggs", "oat milk", "vegan"), and checked against the reviewed allergen matrix.
  "May contain" counts as contains; items never reviewed are never offered.
  "Is there milk in it?" asks about milk without avoiding it, so it doesn't count.
- **Nothing is invented.** No match means the agent says it doesn't have it.
- **Every unmet request is counted.** Searches are already in the event log;
  `GET /insights/unmet-requests` ranks the close and missing ones ("oat milk shake",
  asked 41 times this week), the first real piece of the insights feature.
  The **Insights page** (<http://localhost:5174/insights>, or `/insights` under
  `make demo`, linked from the staff and live screens) shows them ranked, with how
  often, whether the menu came close, when each was last asked, and a suggested
  action for the top one. It refreshes every 30 seconds.

Against the real model: *"Sorry, we don't have an oat milkshake. We do have
cola, lemon-lime soda, or iced tea, none listed as containing milk."*

## Halal and kosher: a reviewed list, and a swap in one step

"Is your chicken halal?" sounds like general knowledge, and that is the trap. Fish
is generally halal, but a fish fillet fried beside non-halal chicken may not be
acceptable, and nothing from a kitchen without kosher supervision is kosher, fish
included. Only the restaurant knows its fryers and certificates, so the answer is
**data someone reviewed**, like the allergen matrix, not the model's judgement.

Each item can carry a `diets` entry in the menu file, with one of two levels and
a note the agent reads out:

| Level | Means | The agent may say |
|---|---|---|
| `certified` | The restaurant holds a certificate | "It's halal" |
| `suitable` | No prohibited ingredients, prepared apart, no certificate | "Suitable for guests eating halal, but not certified", plus the note |
| *(nothing)* | Not listed | That it isn't certified |

In the demo menu nothing is certified, and only the new **fish burger** is listed,
as halal-suitable ("fried in its own fryer, apart from the chicken"). The veggie
burger is not: it shares the grill with the beef. Nothing is kosher.

The `check_diet` tool answers for one item or for every line in the order. It
names the lines that aren't listed and offers in-stock suitable items (from the
same part of the menu first: a burger for a burger). Its instruction to the agent
forbids calling anything halal unless it is certified. A halal question sent to
knowledge search instead comes back pointing at `check_diet`.

**Mid-order swaps never drop an item silently.** "The fish burger *instead*" is
the guest's say-so, so `replace_item` swaps the line in one step: the line keeps
its number and quantity, and the combo is recalculated. "I'll have the fish burger"
alone is ambiguous, so the agent asks "instead, or as well?". The new item's stock
is held before the old one is released, inside the gateway's savepoint, so a sold-out
fish burger leaves the cheeseburger where it was.

Against the real model: *"The cheeseburger isn't listed as halal-certified. The
fish burger is suitable for guests eating halal, though it isn't certified; would
you like to swap it for the cheeseburger?"* / "Okay, the fish burger instead." /
*"I've swapped the cheeseburger for the fish burger."*

The table is `diet_suitability` (item, diet, status, note), made to match the file
on every seed; the words are recognised in `ordering/diet_words.py` and the reads
are in `ordering/diet.py`.

## Caller memory: "what did I order last time?"

Phone orders are a session with `channel = phone` and, unless the number was
withheld, a **caller**: one row per phone number, in E.164 (+14165550123), with
when it first and last called and how many times. Nothing else is kept about a
caller: no name, no address, no payment details. Their orders are not copied
anywhere; they are found through the sessions that link to the number, from the
frozen order rows.

- **The last five orders** come back newest first, with the restaurant's date
  and each line's choices, but no totals: prices change, and a past total would
  be heard as today's.
- **"The same again"** is `reorder`, which proposes each past line to the policy
  gateway as an ordinary `add_item`. Today's prices, stock and combos apply; a
  line since sold out or taken off the menu is refused on its own and the agent
  says so. It can only reach the caller's own recent orders.
- **"Don't save my number"** is `forget_my_orders`: nothing is read back from
  then on. The number itself is kept to help with pickups (no-shows come next),
  which the opening sentence of a call will say.
- **Two calls from one number at the same moment** are both counted: the caller
  row is written with one `INSERT ... ON CONFLICT DO UPDATE`, and a test fires
  five at once to prove it.
- **Typing a caller number is off.** A real caller's number will come only from
  the phone line, which can't be typed over. `ALLOW_TYPED_CALLER_ID=true` lets
  `POST /sessions` take `{"channel": "phone", "caller_phone": "..."}` for
  testing; with it on, anyone could claim a number and hear its orders.

Numbers are parsed in `ordering/phone_numbers.py`: a SIP `From` header, a `tel:`
link or a typed number all come out the same; a withheld number ("anonymous")
or an impossible one comes out as none.

Against the real model: *"Your last order was yesterday: a cheeseburger with
bacon, large plain fries, and a large cola. Would you like that again?"*

> **Privacy (not legal advice).** In Ontario, PIPEDA applies: say why a number
> is kept when it is collected, keep only what that needs and only as long as
> needed, and let people opt out or ask for deletion. Quebec's Law 25 is
> stricter about profiling. The phone line's opening sentence, retention limits
> and staff deletion arrive with the phone line and no-show pieces; have the
> wording reviewed before a real launch.

## Policy engine

The model proposes; code decides. Every action the agent proposes goes
through `src/drivetruagent/policy/` and gets one of four decisions:

| Decision | Meaning | What happens |
|---|---|---|
| `allow` | Fine | The action runs and the new cart comes back |
| `reject` | Not possible | Nothing changes; the reason comes back as a code |
| `confirm` | Unusual; check with the customer first | Nothing changes; the action is stored and its id comes back |
| `escalate` | A person should take over | The session ends as handed off |

None of these rules live in a prompt. A model that ignores its instructions
still cannot add 1,000 fries or submit an order nobody confirmed.

**The rules**

- **State.** Each session state allows only certain actions. An order can be
  submitted only after the agent has read it back (`request_confirmation`),
  and any cart change means reading it back again.
- **Quantity.** More than 10 of one item is unusual and is confirmed first.
  More than 25, or a cart of more than 50 items, is extreme: the customer is
  still asked first, because mishearing is the likeliest cause, and if they
  say yes a person takes over. The three numbers are settings.
- **No prices.** The actions are a fixed set of typed shapes, and none has a
  field for a price, a discount or a total.

**Confirming**

When the decision is `confirm`, the action itself is stored. If the customer
says yes, that stored copy is what runs, so it cannot change between asking
and confirming. A stored action goes stale as soon as the cart changes or
another action is proposed, so "yes" always answers the question just asked.

Every proposal returns the same shape whatever the decision: the decision,
the rule behind it, a short instruction for the agent, and the current cart.

## API

Run `make dev` and open <http://127.0.0.1:8000/docs> to try these.

| Method and path | Does |
|---|---|
| `POST /sessions` | Open a session and its cart |
| `GET /sessions/{id}` | Read a session |
| `PATCH /sessions/{id}` | Set the session's language |
| `POST /sessions/{id}/voice` | Start a voice call: the browser's WebRTC offer in, OpenAI's answer out |
| `DELETE /sessions/{id}/voice` | Hang up |
| `POST /sessions/{id}/pause` | Stop the session's timers |
| `POST /sessions/{id}/resume` | Start them again |
| `GET /sessions/{id}/cart` | Read the cart, priced |
| `POST /sessions/{id}/cart/lines` | Add an item |
| `PATCH /sessions/{id}/cart/lines/{line_no}` | Change quantity, options, or both |
| `DELETE /sessions/{id}/cart/lines/{line_no}` | Remove a line |
| `POST /sessions/{id}/order` | Submit the cart |
| `GET /orders/{id}` | Read a submitted order |
| `POST /sessions/{id}/messages` | Say something to the agent; returns its reply and the cart |
| `GET /sessions/{id}/events` | The session's event log; `?after=` returns only newer events |
| `GET /agent` | Which model the agent uses, and whether it is the scripted demo |
| `GET /inventory` | Stock levels: on hand, reserved, available |
| `PUT /inventory/{code}` | Set how many of an item are on hand |
| `POST /sessions/{id}/actions` | Propose an action; returns the policy decision |
| `POST /sessions/{id}/pending-actions/{pid}/confirm` | The customer said yes to a held action |
| `POST /sessions/{id}/pending-actions/{pid}/cancel` | The customer said no |
| `GET /menu` | The whole menu by category |
| `GET /menu/search?q=` | Find items matching what a customer said |
| `GET /menu/items/{code}` | One item with its choices |
| `GET /menu/items/{code}/allergens` | One item's allergens, and which choices add more |
| `GET /menu/items/{code}/photo` | The item's photo; `?v=` (its version) makes it cacheable for good |
| `GET /admin/menu` | Every item for the menu page, those taken off too |
| `POST /admin/menu/items` | Add an item |
| `PATCH /admin/menu/items/{code}` | Change an item; `is_active: false` takes it off the menu |
| `PUT /admin/menu/items/{code}/photo` | Set or replace its photo (the image is the body) |
| `DELETE /admin/menu/items/{code}/photo` | Remove its photo |
| `GET /insights/unmet-requests?days=7` | What guests asked for that the menu couldn't fill, most asked first |
| `GET /handoffs` | The handoff queue, longest waiting first; `?status=`, `?session_id=` |
| `GET /handoffs/{id}` | One handoff's package |
| `POST /handoffs/{id}/acknowledge` | Say you have picked it up |
| `POST /handoffs/{id}/resolve` | Place the order as it stands, or cancel it |
| `GET /combos` | The combo deals |
| `GET /specials` | Today's specials, with live stock |
| `GET /specials/week` | This week's specials, Monday to Sunday, with today marked |
| `PUT /specials/{code}` | End a special early, or bring it back |

The three action endpoints are the agent's path and go through the policy
engine. The cart endpoints act directly and are for staff and back-office
use, where a person has already made the judgement.

No endpoint holds a rule: each calls an ordering function, commits, and
returns. The agent will call the same functions in-process, so the HTTP API
and the agent's tools cannot disagree.

Every expected failure has one shape, and a caller reacts to the code:

```json
{
  "error": {
    "code": "missing_required_options",
    "message": "'fries' still needs a choice for: size.",
    "details": {"item_code": "fries", "missing": [{"group_code": "size", "options": ["..."]}]}
  }
}
```

| Status | Meaning |
|---|---|
| 404 | The session, cart line or order does not exist |
| 409 | The current state does not allow it: lane busy, session ended, item unavailable or out of stock, empty cart |
| 422 | The menu or the rules do not allow it: unknown item or option, a missing choice, too many options |

### Menu search

`GET /menu/search?q=un cheeseburger` returns ranked matches. The search is
deterministic, with no model and no vector search: it compares the words
against names and aliases in both languages, ignoring case, accents, plurals
and filler words. A vague word such as "burger" returns several items with
equal scores, which tells the agent to ask which one.

## Health endpoints

| Endpoint | Meaning |
|---|---|
| `GET /health/live` | The process is running. Never touches the database. |
| `GET /health/ready` | The app can serve requests. Returns 503 if the database cannot be reached. |

## Project layout

```
src/drivetruagent/
├── main.py          builds the FastAPI app
├── config.py        settings from environment variables
├── api/             HTTP endpoints and error mapping
├── db/              engine, sessions and table models
├── ordering/        the rules: sessions, carts, pricing, orders, menu search, allergens
├── knowledge/       approved documents: parsing, chunking, embedding, ingest and search
├── policy/          allow, reject, confirm or escalate each proposed action
├── agent/           the model, its tools and instructions, the turn loop, the event log
├── evals/           scenarios, the checker, and labelled retrieval questions
├── seed.py          loads the menu file into the database
├── ingest.py        loads and embeds the knowledge documents
└── telemetry.py     sends traces to Arize Phoenix when TRACING_ENDPOINT is set
data/menu.json       the menu: items, choices, prices, aliases, allergens
knowledge/           approved documents the agent may quote (synthetic)
frontend/src/
├── api/             typed client; types generated from the backend
├── session/         the live session: fetching, and what to show (no JSX)
└── views/           the screen in three looks, plus the pieces they share
migrations/          Alembic migration scripts
tests/               unit and integration tests
```
