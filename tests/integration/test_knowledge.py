"""Knowledge search: ingest, search and the agent's tool, against a real
Postgres with pgvector. The local hashing embedder stands in for OpenAI's,
so these run with no key and give the same scores every time."""

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.tools import NO_SOURCE, ToolContext, run_tool
from drivetruagent.db.models import (
    Handoff,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEmbedding,
)
from drivetruagent.enums import KnowledgeAudience, Language, SessionState
from drivetruagent.evals.retrieval import RETRIEVAL_CASES
from drivetruagent.ingest import load_documents
from drivetruagent.knowledge.documents import SourceDocument, parse_document
from drivetruagent.knowledge.embedding import EmbeddingUnavailable, HashingEmbedder
from drivetruagent.knowledge.store import KnowledgeSearch, ingest, search
from drivetruagent.ordering.sessions import get_session, open_session
from drivetruagent.policy.decisions import PolicyLimits
from tests.integration.conftest import KNOWLEDGE_DIR

HASHING = HashingEmbedder()
SEARCH = KnowledgeSearch(HASHING)
LIMITS = PolicyLimits(
    line_quantity_confirm_above=10,
    line_quantity_escalate_above=25,
    cart_items_escalate_above=50,
)


class CountingEmbedder(HashingEmbedder):
    """The hashing embedder, counting how many texts it was asked to embed."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return await super().embed(texts)


class BrokenEmbedder(HashingEmbedder):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingUnavailable("down")


def doc(
    code: str, body: str, *, audience: str = "customer", items: str = "[]"
) -> SourceDocument:
    return parse_document(
        f'+++\nid = "{code}"\ntitle = "{code.title()}"\ntype = "ingredients"\n'
        f'audience = "{audience}"\nitems = {items}\nversion = "1"\n+++\n{body}'
    )


async def count(db: AsyncSession, model: type) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


# --- Ingest -----------------------------------------------------------------


async def test_the_real_documents_are_chunked_and_embedded(
    db_session: AsyncSession,
) -> None:
    documents = load_documents(KNOWLEDGE_DIR)

    report = await ingest(db_session, documents, [HASHING])

    chunks = sum(len(d.chunks) for d in documents)
    assert (report.documents, report.chunks_written) == (len(documents), chunks)
    assert report.embedded == {"hashing-v1": chunks}
    assert await count(db_session, KnowledgeEmbedding) == chunks


async def test_ingesting_again_embeds_nothing(db_session: AsyncSession) -> None:
    documents = load_documents(KNOWLEDGE_DIR)
    await ingest(db_session, documents, [HASHING])
    counting = CountingEmbedder()

    report = await ingest(db_session, documents, [counting])

    assert counting.texts == []
    assert (report.chunks_written, report.chunks_removed) == (0, 0)


async def test_only_a_changed_section_is_embedded_again(
    db_session: AsyncSession,
) -> None:
    await ingest(
        db_session, [doc("oil", "## Oil\nCanola.\n## Salt\nSea salt.")], [HASHING]
    )
    counting = CountingEmbedder()

    report = await ingest(
        db_session, [doc("oil", "## Oil\nSunflower.\n## Salt\nSea salt.")], [counting]
    )

    assert counting.texts == ["Oil · Oil\n\nSunflower."]
    assert (report.chunks_written, report.chunks_kept) == (1, 1)
    assert await count(db_session, KnowledgeEmbedding) == 2


async def test_a_section_taken_out_is_removed_with_its_vectors(
    db_session: AsyncSession,
) -> None:
    await ingest(
        db_session, [doc("oil", "## Oil\nCanola.\n## Salt\nSea salt.")], [HASHING]
    )

    report = await ingest(db_session, [doc("oil", "## Oil\nCanola.")], [HASHING])

    assert report.chunks_removed == 1
    assert (
        await count(db_session, KnowledgeChunk),
        await count(db_session, KnowledgeEmbedding),
    ) == (1, 1)


async def test_a_document_taken_out_is_retired_and_never_found(
    db_session: AsyncSession,
) -> None:
    await ingest(
        db_session,
        [doc("oil", "## Oil\nCanola oil."), doc("salt", "## Salt\nSea salt.")],
        [HASHING],
    )

    report = await ingest(db_session, [doc("salt", "## Salt\nSea salt.")], [HASHING])

    assert report.retired == 1
    retired = (
        await db_session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.code == "oil")
        )
    ).scalar_one()
    assert retired.is_current is False
    found = await SEARCH.find(db_session, "canola oil")
    assert [hit.document for hit in found.hits] == []


async def test_a_retired_document_put_back_is_searched_again(
    db_session: AsyncSession,
) -> None:
    oil = doc("oil", "## Oil\nCanola oil.")
    await ingest(db_session, [oil], [HASHING])
    await ingest(db_session, [], [HASHING])

    await ingest(db_session, [oil], [HASHING])

    assert [h.document for h in (await SEARCH.find(db_session, "canola oil")).hits] == [
        "oil"
    ]


async def test_two_documents_with_one_id_are_refused(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="same id"):
        await ingest(
            db_session, [doc("oil", "## A\nx"), doc("oil", "## B\ny")], [HASHING]
        )


async def test_each_embedder_gets_its_own_vectors(db_session: AsyncSession) -> None:
    class OtherModel(HashingEmbedder):
        model = "other-v1"

    report = await ingest(
        db_session, [doc("oil", "## Oil\nCanola.")], [HASHING, OtherModel()]
    )

    assert report.embedded == {"hashing-v1": 1, "other-v1": 1}


# --- Search -----------------------------------------------------------------


async def test_the_closest_passage_comes_first(
    db_session: AsyncSession, knowledge: None
) -> None:
    found = await SEARCH.find(db_session, "Do you fry in peanut oil?")

    assert found.hits[0].document == "fryer-and-oil"
    assert found.hits[0].heading == "What oil we fry in"
    assert found.best_score == found.hits[0].score >= SEARCH.threshold


async def test_staff_documents_are_never_returned_to_the_customer(
    db_session: AsyncSession, knowledge: None
) -> None:
    """Even asked for word for word."""
    staff_text = (
        "Manager overrides are entered with the shift manager's personal PIN, "
        "which is never shared with customers or other staff."
    )

    found = await SEARCH.find(db_session, staff_text)

    assert all(hit.document != "complaint-resolution" for hit in found.hits)
    as_staff = await SEARCH.find(
        db_session, staff_text, audience=KnowledgeAudience.STAFF
    )
    assert as_staff.hits[0].document == "complaint-resolution"


async def test_an_item_limits_the_search_to_documents_about_it_or_everything(
    db_session: AsyncSession, knowledge: None
) -> None:
    for_cookie = await SEARCH.find(
        db_session, "What oil do you fry in?", item_code="cookie"
    )

    documents = {hit.document for hit in for_cookie.hits}
    assert "fryer-and-oil" not in documents
    assert documents <= {"desserts-and-shakes", "nutrition", "kitchen-and-diets"}


async def test_an_off_topic_question_finds_no_evidence_but_reports_its_best_score(
    db_session: AsyncSession, knowledge: None
) -> None:
    found = await SEARCH.find(db_session, "What's the capital of France?")

    assert found.hits == ()
    assert found.best_score is not None
    assert found.best_score < found.min_score


async def test_vectors_from_another_model_are_never_compared(
    db_session: AsyncSession, knowledge: None
) -> None:
    vector = HASHING.embed_one("peanut oil")

    found = await search(
        db_session,
        vector,
        model="text-embedding-3-small",
        audience=KnowledgeAudience.CUSTOMER,
    )

    assert found == []


async def test_retrieval_quality_with_the_hashing_embedder(
    db_session: AsyncSession, knowledge: None
) -> None:
    """The labelled questions the retrieval evals use, scored locally. The
    hashing embedder only matches words, so French and paraphrases are left
    to the real embedder; everything else must be found."""
    answerable = [
        c for c in RETRIEVAL_CASES if c.document and c.language is Language.EN
    ]
    in_top_three = above_threshold = 0
    for case in answerable:
        found = await SEARCH.find(db_session, case.question, item_code=case.item_code)
        top = await search(
            db_session,
            HASHING.embed_one(case.question),
            model=HASHING.model,
            audience=KnowledgeAudience.CUSTOMER,
            item_code=case.item_code,
        )
        in_top_three += any(hit.document == case.document for hit in top)
        above_threshold += any(hit.document == case.document for hit in found.hits)

    assert in_top_three == len(answerable)
    assert above_threshold >= len(answerable) - 2
    for case in (c for c in RETRIEVAL_CASES if c.document is None):
        assert (await SEARCH.find(db_session, case.question)).hits == (), case.question


# --- The agent's tool -------------------------------------------------------


@pytest.fixture
async def context(db_session: AsyncSession, menu: None, knowledge: None) -> ToolContext:
    session_id = (await open_session(db_session)).id
    return ToolContext(
        db_session, session_id, Decimal("0.13"), LIMITS, knowledge=SEARCH
    )


async def ask(
    context: ToolContext, query: str, item_code: str | None = None
) -> dict[str, Any]:
    return await run_tool(
        context, "search_knowledge", {"query": query, "item_code": item_code}
    )


async def test_the_tool_returns_passages_with_their_ids_and_scores(
    context: ToolContext,
) -> None:
    result = await ask(context, "Is the veggie burger vegan?", "veggie_burger")

    first = result["sources"][0]
    assert first["document"] == "veggie-burger"
    assert "Is the veggie burger vegan" in [s["heading"] for s in result["sources"]]
    assert uuid.UUID(first["chunk_id"])
    assert first["score"] >= SEARCH.threshold
    assert "Answer only from these passages" in result["message"]
    assert result["retrieval"]["model"] == "hashing-v1"


async def test_with_no_evidence_the_agent_is_told_to_say_it_does_not_know(
    db_session: AsyncSession, context: ToolContext
) -> None:
    result = await ask(context, "What time do you close tonight?")

    assert (result["sources"], result["message"]) == ([], NO_SOURCE)
    assert result["retrieval"]["best_score"] < result["retrieval"]["min_score"]
    assert (
        await get_session(db_session, context.session_id)
    ).state is SessionState.GREETING


async def test_an_allergen_question_with_no_evidence_goes_to_a_person(
    db_session: AsyncSession, context: ToolContext
) -> None:
    question = "Is the coffee roasted somewhere that handles sesame?"

    result = await ask(context, question, "coffee")

    assert (result["decision"], result["rule"]) == ("escalate", "allergen_unverified")
    assert result["sources"] == []
    handoff = (
        await db_session.execute(
            select(Handoff).where(Handoff.session_id == context.session_id)
        )
    ).scalar_one()
    assert handoff.details == {
        "item_codes": ["coffee"],
        "items": ["Coffee"],
        "allergens": ["sesame"],
        "question": question,
    }


async def test_an_unknown_item_is_an_error_the_agent_can_read(
    context: ToolContext,
) -> None:
    result = await ask(context, "What oil?", "lobster_roll")

    assert result["error"]["code"] == "unknown_item"


async def test_with_search_down_nothing_is_guessed(
    db_session: AsyncSession, menu: None, knowledge: None
) -> None:
    session_id = (await open_session(db_session)).id
    down = ToolContext(
        db_session,
        session_id,
        Decimal("0.13"),
        LIMITS,
        knowledge=KnowledgeSearch(BrokenEmbedder()),
    )

    plain = await ask(down, "What oil do you fry in?")
    allergen = await ask(down, "Do you fry in peanut oil?")

    assert (plain["sources"], plain["message"]) == ([], NO_SOURCE)
    assert allergen["rule"] == "allergen_unverified"


async def test_with_no_knowledge_configured_nothing_is_found(
    db_session: AsyncSession, menu: None
) -> None:
    session_id = (await open_session(db_session)).id
    bare = ToolContext(db_session, session_id, Decimal("0.13"), LIMITS)

    result = await ask(bare, "What oil do you fry in?")

    assert (result["sources"], result["retrieval"]["model"]) == ([], None)
