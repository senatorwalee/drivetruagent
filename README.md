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
| Later | Stock reservations, voice, human handoff, knowledge search, tracing | Planned |

## Stack

| Layer | Choice |
|---|---|
| Language and API | Python 3.13, FastAPI, Pydantic |
| Database | PostgreSQL 17 with pgvector |
| Database access | SQLAlchemy 2.0 (async), Alembic migrations |
| Model | OpenAI, through the Responses API; the model name is a setting |
| Voice (planned) | OpenAI Realtime, relayed through the backend |
| Tests | pytest, with a real Postgres started by testcontainers |
| Tooling | uv, ruff, pyright, pre-commit, GitHub Actions |

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
| `make lint` | Run ruff and pyright |
| `make format` | Fix lint issues and format the code |
| `make test` | Run every test except the evals (needs Docker) |
| `make test-unit` | Run the fast tests only (no Docker) |
| `make check` | Everything CI runs: lint, then all tests |

## Testing

Tests are split in two:

- `tests/unit` needs no database or network and runs in about a second.
- `tests/integration` runs against a real Postgres. The test run starts its
  own container, applies the real migrations, and rolls each test back, so
  tests never touch your local database and never see each other's data.

Three layers check every change:

| When | What runs |
|---|---|
| On each commit (git hook) | ruff, pyright, unit tests |
| Before each push (git hook) | The full test suite |
| On each pushed commit (GitHub Actions) | Lint, format check, pyright, full test suite |

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
total is not subtotal plus tax.

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

```bash
make eval
```

Evals call the real model, so they need a key and cost money. They are left
out of `make test` and CI, and are skipped if no key is set. The scenario
checker and runner themselves are tested with the scripted model.

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
| `POST /sessions/{id}/actions` | Propose an action; returns the policy decision |
| `POST /sessions/{id}/pending-actions/{pid}/confirm` | The customer said yes to a held action |
| `POST /sessions/{id}/pending-actions/{pid}/cancel` | The customer said no |
| `GET /menu` | The whole menu by category |
| `GET /menu/search?q=` | Find items matching what a customer said |
| `GET /menu/items/{code}` | One item with its choices |

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
| 409 | The current state does not allow it: lane busy, session ended, item unavailable, empty cart |
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
migrations/          Alembic migration scripts
tests/               unit and integration tests
```
