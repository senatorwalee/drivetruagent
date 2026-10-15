"""The embedding contract and the local hashing embedder. No service."""

import math
from types import SimpleNamespace
from typing import Any

import openai
import pytest

from drivetruagent.agent.openai_model import OpenAIEmbedder
from drivetruagent.db.models.knowledge import EMBEDDING_DIMENSIONS
from drivetruagent.knowledge.embedding import (
    DIMENSIONS,
    EmbeddingUnavailable,
    HashingEmbedder,
)

HASHING = HashingEmbedder()


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_the_table_column_and_the_embedders_agree_on_length() -> None:
    assert EMBEDDING_DIMENSIONS == DIMENSIONS


# --- The hashing embedder ---------------------------------------------------


def test_vectors_have_the_shared_length_and_unit_length() -> None:
    vector = HASHING.embed_one("What oil do you fry in?")

    assert len(vector) == DIMENSIONS
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0)


def test_the_same_text_always_gives_the_same_vector() -> None:
    assert HASHING.embed_one("Peanut oil?") == HashingEmbedder().embed_one(
        "Peanut oil?"
    )


def test_text_with_no_words_left_is_a_zero_vector() -> None:
    assert HASHING.embed_one("is the of ?") == [0.0] * DIMENSIONS


def test_shared_words_score_higher_than_unrelated_ones() -> None:
    passage = HASHING.embed_one("All fried items are cooked in canola oil.")
    related = HASHING.embed_one("What oil are the fries cooked in?")
    unrelated = HASHING.embed_one("Who won the hockey game?")

    assert cosine(passage, related) > HASHING.min_score > cosine(passage, unrelated)


def test_case_accents_and_plurals_do_not_matter() -> None:
    assert HASHING.embed_one("Crème BRÛLÉE nuts") == HASHING.embed_one(
        "creme brulee nut"
    )


async def test_embed_returns_one_vector_per_text_in_order() -> None:
    vectors = await HASHING.embed(["oil", "salt"])

    assert vectors == [HASHING.embed_one("oil"), HASHING.embed_one("salt")]


# --- OpenAI's embedder, with the client stood in for ------------------------


class EmbeddingsClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.request: dict[str, Any] = {}
        self._error = error
        self.embeddings = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs: Any) -> Any:
        self.request = kwargs
        if self._error:
            raise self._error
        # Returned out of order, as the API is allowed to.
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=1, embedding=[0.0, 1.0]),
                SimpleNamespace(index=0, embedding=[1.0, 0.0]),
            ]
        )


def embedder(client: EmbeddingsClient) -> OpenAIEmbedder:
    return OpenAIEmbedder(
        api_key="unused",
        model="text-embedding-3-small",
        client=client,  # pyright: ignore[reportArgumentType]
    )


async def test_openai_gets_the_shared_length_and_answers_come_back_in_order() -> None:
    client = EmbeddingsClient()

    vectors = await embedder(client).embed(["first", "second"])

    assert client.request == {
        "model": "text-embedding-3-small",
        "input": ["first", "second"],
        "dimensions": DIMENSIONS,
    }
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]


async def test_nothing_to_embed_makes_no_request() -> None:
    client = EmbeddingsClient()

    assert await embedder(client).embed([]) == []
    assert client.request == {}


async def test_an_openai_failure_becomes_embedding_unavailable() -> None:
    client = EmbeddingsClient(error=openai.OpenAIError("down"))

    with pytest.raises(EmbeddingUnavailable, match="down"):
        await embedder(client).embed(["oil"])
