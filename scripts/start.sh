#!/bin/sh
# Starts the API in its container: migrate, load the menu and the knowledge
# documents, then serve. Each step is safe to repeat on every start.
set -e

# No model key and no choice made: play the scripted demo, which needs none.
if [ -z "$AGENT_MODEL" ] && [ -z "$OPENAI_API_KEY" ]; then
    export AGENT_MODEL=demo
    echo "No OPENAI_API_KEY: using the scripted demo (AGENT_MODEL=demo)."
fi

# A .env written for running on the host sends traces to localhost; inside
# the compose network, Phoenix is the "phoenix" service.
if [ -n "$TRACING_ENDPOINT" ]; then
    export TRACING_ENDPOINT=$(echo "$TRACING_ENDPOINT" | sed 's#//localhost:#//phoenix:#; s#//127\.0\.0\.1:#//phoenix:#')
fi

alembic upgrade head
python -m drivetruagent.seed
# Without it the agent still takes orders; knowledge search just finds nothing.
python -m drivetruagent.ingest || echo "Knowledge ingest failed; carrying on without it."

exec uvicorn drivetruagent.main:app --host 0.0.0.0 --port 8000
