"""Retrieval on its own, with the real embedder: are the right passages
found, and does the threshold separate evidence from noise?

    uv run pytest -m eval -s tests/integration/evals/test_retrieval.py

Judged apart from the agent's answers, so a retrieval problem is not hidden
by a model that guesses well, or blamed on one that words things badly.
Skipped without an OpenAI key, or with the scripted demo selected.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.factory import embedder_from_settings
from drivetruagent.config import Settings
from drivetruagent.enums import KnowledgeAudience
from drivetruagent.evals.retrieval import RETRIEVAL_CASES
from drivetruagent.ingest import load_documents
from drivetruagent.knowledge.embedding import HashingEmbedder
from drivetruagent.knowledge.store import KnowledgeSearch, ingest, search
from tests.integration.conftest import KNOWLEDGE_DIR, TEST_TAX_RATE


@pytest.fixture
def real_search() -> KnowledgeSearch:
    values = {"database_url": "postgresql+psycopg://unused", "tax_rate": TEST_TAX_RATE}
    settings = Settings(**values)  # pyright: ignore[reportArgumentType]
    embedder = embedder_from_settings(settings)
    if isinstance(embedder, HashingEmbedder):
        pytest.skip("needs OPENAI_API_KEY and a real model selected")
    return KnowledgeSearch(embedder)


async def test_the_right_passage_is_found_and_noise_stays_below_the_threshold(
    db_session: AsyncSession, real_search: KnowledgeSearch
) -> None:
    await ingest(db_session, load_documents(KNOWLEDGE_DIR), [real_search.embedder])
    embedder = real_search.embedder
    vectors = await embedder.embed([case.question for case in RETRIEVAL_CASES])

    failures: list[str] = []
    rows: list[str] = []
    for case, vector in zip(RETRIEVAL_CASES, vectors, strict=True):
        top = await search(
            db_session,
            vector,
            model=embedder.model,
            audience=KnowledgeAudience.CUSTOMER,
            item_code=case.item_code,
        )
        best = top[0] if top else None
        rows.append(
            f"{best.score if best else 0:.3f}  {best.document if best else '-':22}"
            f"  want {case.document or '(nothing)':22}  {case.question}"
        )
        if case.document is None:
            if best and best.score >= real_search.threshold:
                failures.append(f"off-topic scored {best.score:.3f}: {case.question}")
            continue
        found = next((hit for hit in top if hit.document == case.document), None)
        if found is None:
            failures.append(f"not in the top {len(top)}: {case.question}")
        elif found.score < real_search.threshold:
            failures.append(f"found but scored {found.score:.3f}: {case.question}")

    print(f"\n{embedder.model}, threshold {real_search.threshold}\n" + "\n".join(rows))
    assert not failures, "\n".join(failures)
