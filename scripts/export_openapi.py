"""Write the API's OpenAPI description to a file the frontend generates types from.

    uv run python scripts/export_openapi.py

The output is committed. A test compares it with the running app's schema, so
changing an endpoint without regenerating the frontend's types fails in CI.
"""

import json
from pathlib import Path

from drivetruagent.main import create_app

OUTPUT = (
    Path(__file__).resolve().parents[1] / "frontend" / "src" / "api" / "openapi.json"
)


def render() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUTPUT.write_text(render())
    print(f"Wrote {OUTPUT.relative_to(Path.cwd())}")
