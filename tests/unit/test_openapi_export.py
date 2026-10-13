"""The frontend's copy of the API description must match the app.

The frontend generates its TypeScript types from frontend/src/api/openapi.json.
If an endpoint changes and that file is not regenerated, the frontend would be
compiled against an API that no longer exists. Regenerate with `make gen-api`.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "export_openapi.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_openapi", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_api_description_is_up_to_date() -> None:
    script = load_script()

    assert script.OUTPUT.read_text() == script.render(), (
        "The API changed. Run `make gen-api` and commit the result."
    )
