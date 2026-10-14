"""Turning text into vectors, and a stand-in that needs no service.

Pure logic: no database, no model library. A real embedder (OpenAI's) lives
with the model adapters in `agent/`; this module only says what one must
provide, and supplies the hashing embedder used by tests and the no-key demo.
"""

import hashlib
import itertools
import math
import re
import unicodedata
from collections.abc import Sequence
from typing import Protocol

# Every embedder gives vectors of this length, so one table column holds them
# all. OpenAI's text-embedding-3 models are asked for this many dimensions.
DIMENSIONS = 384


class EmbeddingUnavailable(Exception):
    """The embedding service could not be reached or refused the request."""


class Embedder(Protocol):
    # Stored with each vector: vectors from different models are never compared.
    @property
    def model(self) -> str: ...

    # Cosine similarity at or above which a chunk counts as evidence. Each
    # model spreads its scores differently, so each has its own.
    @property
    def min_score(self) -> float: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector of DIMENSIONS floats per text, each of length 1.
        Raises EmbeddingUnavailable."""
        ...


# Words that say nothing about what a text is about, in English and French.
_STOPWORD_TEXT = """
    a an and are as at be but by can do does for from has have how i if in is
    it its me my of on or our so that the their them there these they this to
    was we what when where which who why will with you your
    au aux avec ce ces dans de des du elle en est et il je la le les leur mais
    ne nous ou par pas pour qu que qui sa se ses son sur ta te tes un une vos
    votre vous y
"""
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())
_WORD = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """A deterministic, local embedder: words and word pairs hashed into the
    vector. Texts score close when they share words, not meaning, so it
    misses paraphrases a real model would catch. Good enough to exercise
    everything around retrieval without a key."""

    model = "hashing-v1"
    # On the labelled questions in evals/retrieval.py, off-topic questions
    # scored at most 0.11; most answering passages 0.2 to 0.7. Questions that
    # share no words with the answer ("decaf", anything in French) are missed.
    min_score = 0.15

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_one(text) for text in texts]

    def embed_one(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        words = _words(text)
        features = [(word, 1.0) for word in words] + [
            (f"{first} {second}", 0.5) for first, second in itertools.pairwise(words)
        ]
        for feature, weight in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4]) % DIMENSIONS
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector))
        return vector if norm == 0 else [value / norm for value in vector]


def _words(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return [_stem(word) for word in _WORD.findall(plain) if word not in _STOPWORDS]


def _stem(word: str) -> str:
    """Plurals made singular, roughly: "fries" and "fry", "nuts" and "nut"."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word
