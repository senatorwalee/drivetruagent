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
| 01a | Menu tables and the seed menu (20 items, English and Canadian French) | Done |
| 01b | Session, cart and order tables | Done |
| 02 | Cart logic, pricing and order submission | Done |
| 03 | Ordering API and menu search | Done |
| 04 | Policy engine: allow, reject, confirm, escalate | Done |
| 05 | Text-mode agent, event log and first evals | Done; evals not yet run against a real model |
| 06 | Web app: the live session screen in three switchable views | Done |
| 07 | Stock reservations and idempotency keys | Done |
| 08a | Combos: discounts worked out from the cart, and suggested once | Done |
| Later | Today's specials, session timers, voice, human handoff, knowledge search, tracing | Planned |

## Stack

| Layer | Choice |
|---|---|
| Language and API | Python 3.13, FastAPI, Pydantic |
| Database | PostgreSQL 17 with pgvector |
| Database access | SQLAlchemy 2.0 (async), Alembic migrations |
| Model | OpenAI, through the Responses API; the model name is a setting |
| Voice (planned) | OpenAI Realtime, relayed through the backend |
| Tests | pytest, with a real Postgres started by testcontainers |
| Web app | React, TypeScript and Vite; TanStack Query; types generated from the API |
| Tooling | uv, ruff, pyright, oxlint, Vitest, pre-commit, GitHub Actions |

## Getting started

You need [uv](https://docs.astral.sh/uv/), Docker and `make`.

```bash
uv sync                  # install Python and the dependencies
cp .env.example .env     # local settings
make hooks               # install the git hooks
make up                  # start Postgres on localhost:5433
make migrate             # apply database migrations
make seed                # load the menu from data/menu.json
make dev                 # run the API on http://127.0.0.1:8000
```

Then open <http://127.0.0.1:8000/docs> for the interactive API page.

Postgres is published on host port 5433, not the usual 5432, so it does not
collide with a Postgres installed directly on your machine.

## Commands

| Command | What it does |
|---|---|
| `make up` / `make down` | Start and stop the local Postgres |
| `make dev` | Run the API with auto-reload |
| `make migrate` | Apply all database migrations |
| `make seed` | Load `data/menu.json` into the database; safe to run repeatedly |
| `make chat` | Talk to the agent in the terminal (needs `OPENAI_API_KEY`) |
| `make eval` | Run the evals against the real model (needs `OPENAI_API_KEY`) |
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
| `add_item`, `update_item`, `remove_item` | The policy gateway |
| `request_confirmation`, `submit_order` | The policy gateway |
| `confirm_pending_action`, `cancel_pending_action` | The customer's answer to a held action |
| `set_language` | Sets the session language |
| `request_human` | Hands the session to a person |

To talk to it, put `OPENAI_API_KEY` in `.env`, then:

```bash
make chat
```

Without a key everything else still works; only the conversation endpoint
answers 503.

### Running it without a key

Set `AGENT_MODEL=demo` in `.env`. The agent then plays one fixed conversation:
a combo offered and taken up, a missing size, two items in one breath, fifteen
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
- **One test suite covers all three views.** They look different but must
  show the same things and offer the same controls.

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

```bash
make eval
```

Evals call the real model, so they need a key and cost money. They are left
out of `make test` and CI, and are skipped if no key is set. The scenario
checker and runner themselves are tested with the scripted model.

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
| `GET /combos` | The combo deals |

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
├── ordering/        the rules: sessions, carts, pricing, orders, menu search
├── policy/          allow, reject, confirm or escalate each proposed action
├── agent/           the model, its tools and instructions, the turn loop, the event log
├── evals/           scenarios and the checker that judges a finished conversation
└── seed.py          loads the menu file into the database
data/menu.json       the menu: items, choices, prices, aliases
frontend/src/
├── api/             typed client; types generated from the backend
├── session/         the live session: fetching, and what to show (no JSX)
└── views/           the screen in three looks, plus the pieces they share
migrations/          Alembic migration scripts
tests/               unit and integration tests
```
