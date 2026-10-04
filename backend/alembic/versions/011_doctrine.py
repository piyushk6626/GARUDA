"""Doctrine chunks for hybrid full-text and vector search."""

from alembic import op

revision = "011_doctrine"
down_revision = "010_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE doc_chunk (
            chunk_id text PRIMARY KEY,
            doc_id text NOT NULL,
            text text NOT NULL,
            embedding vector(3072) NOT NULL,
            meta jsonb NOT NULL DEFAULT '{}'::jsonb,
            search_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
        )
        """
    )
    op.execute(
        """
        CREATE INDEX doc_chunk_embedding_hnsw
            ON doc_chunk USING hnsw (embedding vector_cosine_ops)
        """
    )
    op.execute("CREATE INDEX doc_chunk_search_tsv_gin ON doc_chunk USING gin (search_tsv)")
    op.execute("GRANT SELECT ON doc_chunk TO llm_ro")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS doc_chunk")
