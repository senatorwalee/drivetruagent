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
| 01 | Menu, session, cart and order tables | Next |
| 02 | Cart logic and pricing | Planned |
| 03 | Ordering API | Planned |
| 04 | Policy engine: allow, reject, confirm, escalate | Planned |
| 05 | Text-mode agent and first evals | Planned |
| Later | Stock reservations, voice, human handoff, knowledge search, tracing | Planned |

## Stack

| Layer | Choice |
|---|---|
| Language and API | Python 3.13, FastAPI, Pydantic |
| Database | PostgreSQL 17 with pgvector |
| Database access | SQLAlchemy 2.0 (async), Alembic migrations |
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
| `make lint` | Run ruff and pyright |
| `make format` | Fix lint issues and format the code |
| `make test` | Run every test (needs Docker) |
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
├── api/             HTTP endpoints
└── db/              engine, sessions and table models
migrations/          Alembic migration scripts
tests/               unit and integration tests
```
