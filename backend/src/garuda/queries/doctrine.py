"""Hybrid doctrine search: Postgres full-text plus pgvector, merged by reciprocal rank fusion.

The table is doc_chunk (see retrieval_and_context.md). Callers pass a read-only
SQL port and an embedder; they never send SQL.
"""

import math
from typing import Any

from garuda.contracts.tool import ToolInputError
from garuda.context.ports import DoctrineReader, Embedder

EMBEDDING_DIM = 3072
RRF_K = 60
CANDIDATE_LIMIT = 50
MAX_QUERY_CHARS = 2000

SEARCH_SQL = """
WITH query AS (
    SELECT websearch_to_tsquery('english', $1) AS tsq
),
lexical AS (
    SELECT chunk_id, doc_id, text,
           row_number() OVER (ORDER BY rank_score DESC, chunk_id) AS rank
    FROM (
        SELECT c.chunk_id, c.doc_id, c.text,
               ts_rank_cd(c.search_tsv, q.tsq) AS rank_score
        FROM doc_chunk AS c
        CROSS JOIN query AS q
        WHERE c.search_tsv @@ q.tsq
        ORDER BY rank_score DESC, c.chunk_id
        LIMIT $2
    ) AS hits
),
semantic AS (
    SELECT chunk_id, doc_id, text,
           row_number() OVER (ORDER BY distance, chunk_id) AS rank
    FROM (
        SELECT chunk_id, doc_id, text, embedding <=> $3::vector AS distance
        FROM doc_chunk
        ORDER BY embedding <=> $3::vector
        LIMIT $2
    ) AS hits
),
fused AS (
    SELECT chunk_id, doc_id AS doc, text,
           sum(1.0 / ($4 + rank))::float8 AS score
    FROM (
        SELECT chunk_id, doc_id, text, rank FROM lexical
        UNION ALL
        SELECT chunk_id, doc_id, text, rank FROM semantic
    ) AS ranked
    GROUP BY chunk_id, doc_id, text
)
SELECT text, doc, score
FROM fused
ORDER BY score DESC, chunk_id
LIMIT $5
"""


def reciprocal_rank_fusion(rankings: list[list[str]], *, rrf_k: int = RRF_K) -> list[tuple[str, float]]:
    """Merge ranked id lists. Score is the sum of 1 / (rrf_k + rank), rank starting at 1."""
    if rrf_k < 1:
        raise ToolInputError("rrf_k must be at least 1")
    scores: dict[str, float] = {}
    for ranking in rankings:
        for index, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (rrf_k + index)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def search_args(query: str, embedding: list[float], k: int) -> tuple[Any, ...]:
    """Bound parameters for SEARCH_SQL. The vector is a pgvector literal."""
    return (query, CANDIDATE_LIMIT, vector_literal(embedding), RRF_K, k)


def vector_literal(embedding: list[float]) -> str:
    _check_embedding(embedding)
    return "[" + ",".join(format(value, ".8g") for value in embedding) + "]"


async def search_passages(
    reader: DoctrineReader,
    embedder: Embedder,
    query: str,
    *,
    k: int,
    timeout_s: float,
) -> list[dict[str, Any]]:
    """Return the top passages for query. Each row has text, doc, and an RRF score."""
    text = _check_query(query)
    if k < 1 or k > 20:
        raise ToolInputError("k must be between 1 and 20")
    embedding = await embedder.embed(text)
    rows = await reader.read(SEARCH_SQL, search_args(text, embedding, k), timeout_s=timeout_s)
    return [_passage_row(row) for row in rows]


def _check_query(query: str) -> str:
    text = query.strip()
    if not text:
        raise ToolInputError("query must not be blank")
    if len(text) > MAX_QUERY_CHARS:
        raise ToolInputError(f"query must be at most {MAX_QUERY_CHARS} characters")
    return text


def _check_embedding(embedding: list[float]) -> None:
    if len(embedding) != EMBEDDING_DIM:
        raise ToolInputError(f"embedding length must be {EMBEDDING_DIM}")
    if any(_bad_component(value) for value in embedding):
        raise ToolInputError("embedding values must be finite numbers")


def _bad_component(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    return not math.isfinite(value)


def _passage_row(row: dict[str, Any]) -> dict[str, Any]:
    return {"text": str(row["text"]), "doc": str(row["doc"]), "score": float(row["score"])}
