"""The evals: each scenario played against the real model.

    make eval

These need OPENAI_API_KEY (in the environment or .env) and cost money, so
they are left out of `make test` and CI. Without a key they are skipped.

Each check is deterministic: it looks at the cart, the order, the session's
state and the tools used, not at the agent's wording.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.factory import agent_config, model_from_settings
from drivetruagent.agent.model import ModelClient
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


@pytest.fixture(scope="module")
def model(settings: Settings) -> ModelClient:
    if settings.openai_api_key is None:
        pytest.skip("OPENAI_API_KEY is not set")
    return model_from_settings(settings)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
async def test_scenario(
    db_session: AsyncSession, scenario: Scenario, model: ModelClient, settings: Settings
) -> None:
    result = await run_scenario(
        db_session, scenario, model=model, config=agent_config(settings)
    )

    failures = check(scenario, result)
    assert not failures, "\n".join(
        [
            f"{scenario.name} failed:",
            *(f"  - {f}" for f in failures),
            "",
            result.transcript(),
        ]
    )
