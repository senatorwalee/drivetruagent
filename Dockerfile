# The API, with the menu and the knowledge documents loaded on start.
#
#   docker compose --profile app up --build      (or: make demo)
FROM python:3.13-slim-bookworm

# The same uv that wrote uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so changing the code does not reinstall them.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

COPY alembic.ini ./
COPY migrations ./migrations
COPY data ./data
COPY knowledge ./knowledge
COPY scripts/start.sh ./scripts/start.sh

RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8000
CMD ["sh", "scripts/start.sh"]
