"""The evals: each scenario played against the real model.

    make eval

These run against whichever real model is configured (in the environment or
.env): OpenAI by default, or Gemini with AGENT_MODEL=gemini. They call a real
service, so they are left out of `make test` and CI. With no key, or with the
scripted demo selected, they are skipped.

Each check is deterministic: it looks at the cart, the order, the session's
state and the tools used, not at the agent's wording. To read the wording,
run with -s: every conversation is printed.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.factory import agent_config, model_from_settings
from drivetruagent.agent.model import ModelClient, ModelUnavailable
from drivetruagent.config import Settings
from drivetruagent.evals.runner import run_scenario
from drivetruagent.evals.scenario import Scenario, check
from drivetruagent.evals.scenarios import SCENARIOS
from tests.integration.conftest import TEST_TAX_RATE

pytestmark = pytest.mark.usefixtures("specials")


@pytest.fixture(scope="module")
def settings() -> Settings:
    # The database URL is not used: the tests bring their own database.
    values = {"database_url": "postgresql+psycopg://unused", "tax_rate": TEST_TAX_RATE}
    return Settings(**values)  # pyright: ignore[reportArgumentType]


# Per test, not per module: each test runs in its own event loop, and a client
# kept from an earlier test holds pooled connections tied to a closed loop
# ("Event loop is closed" on the next request).
@pytest.fixture
def model(settings: Settings) -> ModelClient:
    if settings.agent_model == "demo":
        pytest.skip("AGENT_MODEL=demo plays a script; the evals need a real model")
    try:
        return model_from_settings(settings)
    except ModelUnavailable as error:
        pytest.skip(str(error))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
async def test_scenario(
    db_session: AsyncSession, scenario: Scenario, model: ModelClient, settings: Settings
) -> None:
    result = await run_scenario(
        db_session, scenario, model=model, config=agent_config(settings)
    )

    # Shown with -s, to read how the agent worded things.
    print(f"\n{scenario.name}\n{result.transcript()}")
    failures = check(scenario, result)
    assert not failures, "\n".join(
        [
            f"{scenario.name} failed:",
            *(f"  - {f}" for f in failures),
            "",
            result.transcript(),
        ]
    )
