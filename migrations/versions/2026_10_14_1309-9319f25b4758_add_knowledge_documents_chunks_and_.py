"""add knowledge documents, chunks and embeddings

Revision ID: 9319f25b4758
Revises: 1b99e6435dcc
Create Date: 2026-10-14 13:09:40.937887

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9319f25b4758"
down_revision: str | Sequence[str] | None = "1b99e6435dcc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector ships with the database image; this turns it on for the database.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_documents",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column(
            "doc_type",
            sa.Enum(
                "ingredients",
                "preparation",
                "nutrition",
                "diets",
                "procedure",
                name="knowledgetype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "audience",
            sa.Enum(
                "customer",
                "staff",
                name="knowledgeaudience",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "item_codes",
            sa.ARRAY(sa.String()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column(
            "is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "audience IN ('customer', 'staff')",
            name=op.f("ck_knowledge_documents_audience_valid"),
        ),
        sa.CheckConstraint(
            "doc_type IN "
            "('ingredients', 'preparation', 'nutrition', 'diets', 'procedure')",
            name=op.f("ck_knowledge_documents_doc_type_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_documents")),
        sa.UniqueConstraint("code", name=op.f("uq_knowledge_documents_code")),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            name=op.f("fk_knowledge_chunks_document_id_knowledge_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
        sa.UniqueConstraint(
            "document_id",
            "position",
            name=op.f("uq_knowledge_chunks_document_id_position"),
        ),
    )
    op.create_table(
        "knowledge_embeddings",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column(
            "embedding", pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["knowledge_chunks.id"],
            name=op.f("fk_knowledge_embeddings_chunk_id_knowledge_chunks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_embeddings")),
        sa.UniqueConstraint(
            "chunk_id", "model", name=op.f("uq_knowledge_embeddings_chunk_id_model")
        ),
    )
    op.create_index(
        op.f("ix_knowledge_embeddings_chunk_id"),
        "knowledge_embeddings",
        ["chunk_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_embeddings_embedding_hnsw",
        "knowledge_embeddings",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_embeddings_embedding_hnsw",
        table_name="knowledge_embeddings",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index(
        op.f("ix_knowledge_embeddings_chunk_id"), table_name="knowledge_embeddings"
    )
    op.drop_table("knowledge_embeddings")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
    # The vector extension is left on: dropping it is not this migration's call.
