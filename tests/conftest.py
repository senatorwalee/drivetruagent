from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
INTEGRATION_DIR = TESTS_DIR / "integration"
EVALS_DIR = INTEGRATION_DIR / "evals"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by folder, so `-m "not integration"` and `-m eval` work."""
    for item in items:
        if INTEGRATION_DIR in item.path.parents:
            item.add_marker(pytest.mark.integration)
        if EVALS_DIR in item.path.parents:
            item.add_marker(pytest.mark.eval)
