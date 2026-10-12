.PHONY: up down dev migrate seed lint format test test-unit check hooks

# Start and stop the local Postgres.
up:
	docker compose up -d --wait

down:
	docker compose down

# Run the API with auto-reload.
dev:
	uv run uvicorn drivetruagent.main:app --reload

# Apply all database migrations to the local Postgres.
migrate:
	uv run alembic upgrade head

# Load data/menu.json into the local Postgres. Safe to run repeatedly.
seed:
	uv run python -m drivetruagent.seed

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright

format:
	uv run ruff check --fix .
	uv run ruff format .

# All tests. The integration tests start their own Postgres container.
test:
	uv run pytest

# Fast tests only: no Docker needed.
test-unit:
	uv run pytest -m "not integration"

# Everything CI runs. Use this before pushing.
check: lint test

# Install the git hooks (once per clone).
hooks:
	uv run pre-commit install
