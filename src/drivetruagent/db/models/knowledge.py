"""Approved knowledge documents, cut into chunks, with their embeddings.

Embeddings are kept apart from chunks, one row per embedding model, so the
same text can be searched with OpenAI's embedder and with the local hashing
one. Vectors from different models are never compared.
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from drivetruagent.db.base import (
    Base,
    Timestamps,
    UuidPrimaryKey,
    enum_check,
    text_enum,
)
from drivetruagent.enums import KnowledgeAudience, KnowledgeType

# The vector column's length. Must equal knowledge.embedding.DIMENSIONS, which
# a test checks; it is repeated here so the database layer imports nothing
# above it.
EMBEDDING_DIMENSIONS = 384


class KnowledgeDocument(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        enum_check("doc_type", KnowledgeType),
        enum_check("audience", KnowledgeAudience),
    )

    # The id in the file's front matter.
    code: Mapped[str] = mapped_column(unique=True)
    title: Mapped[str]
    doc_type: Mapped[KnowledgeType] = mapped_column(text_enum(KnowledgeType))
    audience: Mapped[KnowledgeAudience] = mapped_column(text_enum(KnowledgeAudience))
    # Menu item codes it is about. Empty: everything.
    item_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String), server_default=text("'{}'")
    )
    version: Mapped[str]
    content_hash: Mapped[str]
    # False once the file is gone from the folder: kept, never searched.
    is_current: Mapped[bool] = mapped_column(server_default=text("true"))


class KnowledgeChunk(UuidPrimaryKey, Timestamps, Base):
    """One section of a document: what is embedded, and what the agent reads."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("document_id", "position"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE")
    )
    position: Mapped[int]
    heading: Mapped[str]
    text: Mapped[str]
    content_hash: Mapped[str]


class KnowledgeEmbedding(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "knowledge_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "model"),
        # Approximate nearest neighbours by cosine distance.
        Index(
            "ix_knowledge_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), index=True
    )
    # Which embedder made it: "text-embedding-3-small", "hashing-v1".
    model: Mapped[str]
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
