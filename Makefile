.PHONY: up down demo tunnel phoenix dev migrate seed ingest chat ui gen-api lint format test test-unit eval check check-ui hooks

# Start and stop the local Postgres.
up:
	docker compose up -d --wait

down:
	docker compose --profile tracing --profile app down

# The whole app in Docker: database, API (menu and knowledge loaded) and web
# app on http://localhost:8080. Uses .env if there is one; with no key it
# plays the scripted demo.
demo:
	docker compose --profile app up --build -d --wait
	@echo "Open http://localhost:8080 (staff screen: http://localhost:8080/staff)"

# Arize Phoenix on http://localhost:6006. Set TRACING_ENDPOINT to send it traces.
# Make the phone webhook (and only it) reachable from the internet, for
# OpenAI to ring. Needs `make demo` running and cloudflared installed. Prints
# a https://....trycloudflare.com address: the webhook URL is that address
# followed by /phone/incoming.
tunnel:
	cloudflared tunnel --url http://localhost:8081

phoenix:
	docker compose --profile tracing up -d phoenix

# Run the API with auto-reload.
dev:
	uv run uvicorn drivetruagent.main:app --reload

# Apply all database migrations to the local Postgres.
migrate:
	uv run alembic upgrade head

# Load data/menu.json into the local Postgres. Safe to run repeatedly.
seed:
	uv run python -m drivetruagent.seed

# Load the knowledge documents; with OPENAI_API_KEY, OpenAI embeds them too.
ingest:
	uv run python -m drivetruagent.ingest

# Talk to the agent in the terminal. Needs a model key in .env (OpenAI or Gemini).
chat:
	uv run python -m drivetruagent.agent.chat

# Run the web app on http://localhost:5174. It talks to the API from `make dev`.
ui:
	npm --prefix frontend run dev

# Regenerate the frontend's API types after changing an endpoint.
gen-api:
	uv run python scripts/export_openapi.py
	npm --prefix frontend run gen-api

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright

format:
	uv run ruff check --fix .
	uv run ruff format .

# All tests except the evals. The integration tests start their own Postgres
# container.
test:
	uv run pytest

# Fast tests only: no Docker needed.
test-unit:
	uv run pytest -m "not integration and not eval"

# Evals: whole conversations with the real model. Needs a model key (OpenAI
# or Gemini) and calls a real service, so it is never part of `make test` or CI.
eval:
	uv run pytest -m eval -v

# Everything CI runs for the backend. Use this before pushing.
check: lint test

# Everything CI runs for the frontend: lint, type check, tests and a build.
check-ui:
	npm --prefix frontend run check

# Install the git hooks (once per clone).
hooks:
	uv run pre-commit install
