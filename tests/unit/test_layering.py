"""The layers may only depend downwards. These tests read the import lines.

    api  ->  agent  ->  policy  ->  knowledge  ->  ordering  ->  db

A lower layer importing a higher one, or the web framework, is how a tidy
codebase turns into a tangle. Checking it here means it fails in CI instead
of being noticed months later.
"""

import ast
from pathlib import Path

import pytest

PACKAGE = "drivetruagent"
SOURCE = Path(__file__).resolve().parents[2] / "src" / PACKAGE

# Each layer, lowest first. A layer may import the ones before it, not after.
LAYERS = ["db", "ordering", "knowledge", "policy", "agent", "api"]

# Files that are pure logic: plain values in, plain values out.
PURE_MODULES = [
    "cache.py",
    "ordering/selection.py",
    "ordering/pricing.py",
    "ordering/discounts.py",
    "ordering/timers.py",
    "ordering/search.py",
    "ordering/allergens.py",
    "ordering/language.py",
    "ordering/diet_words.py",
    "ordering/photos.py",
    "knowledge/documents.py",
    "knowledge/embedding.py",
    "ordering/errors.py",
    "policy/rules.py",
    "policy/decisions.py",
    "policy/actions.py",
    "policy/handoff_package.py",
    "policy/guardrails.py",
    "agent/messages.py",
    "agent/model.py",
    "evals/scenario.py",
]


def imported_modules(path: Path) -> set[str]:
    """Every module a file imports, as dotted names."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def files_in(layer: str) -> list[Path]:
    return sorted((SOURCE / layer).rglob("*.py"))


def imports_from(path: Path, prefix: str) -> set[str]:
    return {
        module
        for module in imported_modules(path)
        if module == prefix or module.startswith(prefix + ".")
    }


@pytest.mark.parametrize("layer", LAYERS)
def test_every_layer_has_files(layer: str) -> None:
    """Guards the tests below against passing because a folder was renamed."""
    assert files_in(layer)


@pytest.mark.parametrize("layer", LAYERS[:-1])
def test_a_layer_does_not_import_the_layers_above_it(layer: str) -> None:
    higher = LAYERS[LAYERS.index(layer) + 1 :]

    for path in files_in(layer):
        for other in higher:
            found = imports_from(path, f"{PACKAGE}.{other}")
            assert not found, f"{path.relative_to(SOURCE)} imports {sorted(found)}"


@pytest.mark.parametrize("layer", LAYERS[:-1])
def test_only_the_api_layer_uses_the_web_framework(layer: str) -> None:
    for path in files_in(layer):
        found = imports_from(path, "fastapi") | imports_from(path, "starlette")
        assert not found, f"{path.relative_to(SOURCE)} imports {sorted(found)}"


@pytest.mark.parametrize("module", PURE_MODULES)
def test_pure_logic_touches_no_database_web_or_model_library(module: str) -> None:
    path = SOURCE / module
    assert path.exists(), module

    for library in ("sqlalchemy", "fastapi", "openai", "httpx"):
        found = imports_from(path, library)
        assert not found, f"{module} imports {sorted(found)}"
    found = imports_from(path, f"{PACKAGE}.db")
    assert not found, f"{module} imports {sorted(found)}"


def test_only_the_adapters_import_the_model_providers_library() -> None:
    """Everything else talks to a model through the ModelClient interface, or
    for voice through agent/voice.py."""
    users = {
        str(path.relative_to(SOURCE))
        for path in SOURCE.rglob("*.py")
        if imports_from(path, "openai")
    }

    assert users == {"agent/openai_model.py", "agent/voice.py"}


def test_only_the_gemini_adapter_imports_googles_library() -> None:
    users = {
        str(path.relative_to(SOURCE))
        for path in SOURCE.rglob("*.py")
        if imports_from(path, "google")
    }

    assert users == {"agent/gemini_model.py"}


def test_only_the_tracing_modules_import_opentelemetry() -> None:
    """Spans are made in agent/tracing.py and exported from telemetry.py;
    everything else calls agent/tracing.py."""
    users = {
        str(path.relative_to(SOURCE))
        for path in SOURCE.rglob("*.py")
        if imports_from(path, "opentelemetry")
    }

    assert users == {"agent/tracing.py", "telemetry.py"}
