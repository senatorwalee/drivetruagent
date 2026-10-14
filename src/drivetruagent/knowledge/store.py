"""Putting approved documents into the database, and searching them.

Ingest makes the tables match the documents given, and only embeds what is
new: an unchanged chunk keeps its vectors, a changed one is re-embedded, and
a document no longer given is retired, never searched again.

Search is for the customer-facing agent unless told otherwise, and the
audience is a parameter of this function, not of any tool: staff documents
cannot be reached by anything the model sends.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import any_, delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEmbedding,
)
from drivetruagent.enums import KnowledgeAudience
from drivetruagent.knowledge.documents import SourceDocument
from drivetruagent.knowledge.embedding import Embedder

# How many texts go to an embedder in one request.
EMBED_BATCH = 64
DEFAULT_RESULTS = 3


@dataclass(frozen=True)
class IngestReport:
    documents: int
    retired: int
    chunks_written: int
    chunks_kept: int
    chunks_removed: int
    # Model -> vectors made in this run.
    embedded: dict[str, int] = field(default_factory=dict)


async def ingest(
    db: AsyncSession,
    documents: Sequence[SourceDocument],
    embedders: Sequence[Embedder],
) -> IngestReport:
    """Make the knowledge tables match `documents`, embedding with each of
    `embedders`. The caller owns the transaction. Raises ValueError if two
    documents share an id, and EmbeddingUnavailable from an embedder."""
    codes = [document.meta.id for document in documents]
    if len(set(codes)) != len(codes):
        raise ValueError("two knowledge documents have the same id")

    written = kept = removed = 0
    for document in documents:
        document_id = await _upsert_document(db, document)
        counts = await _sync_chunks(db, document_id, document)
        written, kept, removed = (
            written + counts[0],
            kept + counts[1],
            removed + counts[2],
        )

    retired = await db.execute(
        update(KnowledgeDocument)
        .where(KnowledgeDocument.code.not_in(codes), KnowledgeDocument.is_current)
        .values(is_current=False, updated_at=func.now())
    )
    embedded = {
        embedder.model: await _embed_missing(db, embedder) for embedder in embedders
    }
    return IngestReport(
        documents=len(documents),
        retired=retired.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        chunks_written=written,
        chunks_kept=kept,
        chunks_removed=removed,
        embedded=embedded,
    )


async def _upsert_document(db: AsyncSession, document: SourceDocument) -> uuid.UUID:
    meta = document.meta
    values = {
        "code": meta.id,
        "title": meta.title,
        "doc_type": meta.type,
        "audience": meta.audience,
        "item_codes": meta.items,
        "version": meta.version,
        "content_hash": document.content_hash,
        "is_current": True,
    }
    statement = (
        insert(KnowledgeDocument)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["code"],
            set_={**values, "updated_at": func.now()},
        )
        .returning(KnowledgeDocument.id)
    )
    return (await db.execute(statement)).scalar_one()


async def _sync_chunks(
    db: AsyncSession, document_id: uuid.UUID, document: SourceDocument
) -> tuple[int, int, int]:
    """Returns (written, kept, removed)."""
    existing = {
        chunk.position: chunk
        for chunk in (
            await db.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
            )
        ).scalars()
    }
    written = kept = 0
    for chunk in document.chunks:
        current = existing.pop(chunk.position, None)
        if current is not None and current.content_hash == chunk.content_hash:
            kept += 1
            continue
        if current is not None:
            # Changed text: its old vectors no longer describe it.
            await db.execute(
                delete(KnowledgeEmbedding).where(
                    KnowledgeEmbedding.chunk_id == current.id
                )
            )
            current.heading = chunk.heading
            current.text = chunk.text
            current.content_hash = chunk.content_hash
        else:
            db.add(
                KnowledgeChunk(
                    document_id=document_id,
                    position=chunk.position,
                    heading=chunk.heading,
                    text=chunk.text,
                    content_hash=chunk.content_hash,
                )
            )
        written += 1

    for leftover in existing.values():
        await db.delete(leftover)
    await db.flush()
    return written, kept, len(existing)


async def _embed_missing(db: AsyncSession, embedder: Embedder) -> int:
    """Embed every chunk of a current document that has no vector from this
    embedder yet. Returns how many were made."""
    has_vector = select(KnowledgeEmbedding.chunk_id).where(
        KnowledgeEmbedding.model == embedder.model
    )
    missing = (
        await db.execute(
            select(KnowledgeChunk.id, KnowledgeChunk.text)
            .join(KnowledgeDocument)
            .where(KnowledgeDocument.is_current, KnowledgeChunk.id.not_in(has_vector))
            .order_by(KnowledgeChunk.id)
        )
    ).all()
    for start in range(0, len(missing), EMBED_BATCH):
        batch = missing[start : start + EMBED_BATCH]
        vectors = await embedder.embed([text for _, text in batch])
        db.add_all(
            KnowledgeEmbedding(
                chunk_id=chunk_id, model=embedder.model, embedding=vector
            )
            for (chunk_id, _), vector in zip(batch, vectors, strict=True)
        )
    await db.flush()
    return len(missing)


# --- Search -----------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    chunk_id: uuid.UUID
    document: str
    title: str
    heading: str
    text: str
    # Cosine similarity, 1 for the same direction.
    score: float


@dataclass(frozen=True)
class Retrieval:
    query: str
    model: str
    min_score: float
    # At or above min_score, best first: the evidence.
    hits: tuple[Hit, ...]
    # The closest chunk's score, evidence or not. None if nothing is indexed.
    best_score: float | None


@dataclass(frozen=True)
class KnowledgeSearch:
    """How the agent searches: with which embedder, how many results, and
    the score a chunk needs to count."""

    embedder: Embedder
    limit: int = DEFAULT_RESULTS
    # None: the embedder's own threshold.
    min_score: float | None = None

    @property
    def threshold(self) -> float:
        return self.embedder.min_score if self.min_score is None else self.min_score

    async def find(
        self,
        db: AsyncSession,
        query: str,
        *,
        item_code: str | None = None,
        audience: KnowledgeAudience = KnowledgeAudience.CUSTOMER,
    ) -> Retrieval:
        """Raises EmbeddingUnavailable."""
        (vector,) = await self.embedder.embed([query])
        found = await search(
            db,
            vector,
            model=self.embedder.model,
            audience=audience,
            item_code=item_code,
            limit=self.limit,
        )
        threshold = self.threshold
        return Retrieval(
            query=query,
            model=self.embedder.model,
            min_score=threshold,
            hits=tuple(hit for hit in found if hit.score >= threshold),
            best_score=found[0].score if found else None,
        )


async def search(
    db: AsyncSession,
    vector: Sequence[float],
    *,
    model: str,
    audience: KnowledgeAudience,
    item_code: str | None = None,
    limit: int = DEFAULT_RESULTS,
) -> list[Hit]:
    """The closest chunks to `vector` among current documents for `audience`,
    best first, whatever their score. With an item, only documents about it
    or about nothing in particular."""
    distance = KnowledgeEmbedding.embedding.cosine_distance(vector)
    statement = (
        select(KnowledgeChunk, KnowledgeDocument, distance.label("distance"))
        .join(KnowledgeChunk, KnowledgeEmbedding.chunk_id == KnowledgeChunk.id)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeEmbedding.model == model,
            KnowledgeDocument.audience == audience,
            KnowledgeDocument.is_current,
        )
        .order_by(distance)
        .limit(limit)
    )
    if item_code is not None:
        statement = statement.where(
            or_(
                func.cardinality(KnowledgeDocument.item_codes) == 0,
                literal(item_code) == any_(KnowledgeDocument.item_codes),
            )
        )
    rows = (await db.execute(statement)).all()
    return [
        Hit(
            chunk_id=chunk.id,
            document=document.code,
            title=document.title,
            heading=chunk.heading,
            text=chunk.text,
            score=round(1 - float(row_distance), 4),
        )
        for chunk, document, row_distance in rows
    ]
