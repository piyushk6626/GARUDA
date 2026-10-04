"""search_doctrine: hybrid full-text and vector search, fused with reciprocal rank fusion."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.queries.doctrine import (
    CANDIDATE_LIMIT,
    EMBEDDING_DIM,
    RRF_K,
    SEARCH_SQL,
    reciprocal_rank_fusion,
    search_passages,
)
from garuda.tools.data.search_doctrine import SearchDoctrineInput, search_doctrine

WHEN = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)


class FakeReader:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, tuple[Any, ...], float]] = []

    async def read(self, sql: str, args: tuple[Any, ...], *, timeout_s: float) -> list[dict[str, Any]]:
        self.calls.append((sql, args, timeout_s))
        return list(self.rows)


class FakeEmbedder:
    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector if vector is not None else [0.1] * EMBEDDING_DIM
        self.texts: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        return list(self.vector)


def _ctx(reader: Any = None, embedder: Any = None) -> ToolContext:
    blank = SimpleNamespace()
    return ToolContext(
        scenario=blank,
        plans=blank,
        models=blank,
        providers=blank,
        scenario_time=WHEN,
        doctrine=reader,
        embedder=embedder,
    )


def _run(query: str, reader: Any, embedder: Any, k: int = 5) -> Any:
    payload = SearchDoctrineInput(query=query, k=k)
    return asyncio.run(search_doctrine(payload, _ctx(reader, embedder)))


def test_rrf_sums_both_lists_and_breaks_ties_by_id():
    fused = reciprocal_rank_fusion([["b", "a"], ["a", "c"]])

    scores = dict(fused)
    assert scores["a"] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert scores["b"] == pytest.approx(1 / (RRF_K + 1))
    assert [item_id for item_id, _ in fused] == ["a", "b", "c"]


def test_search_sends_the_fixed_select_and_maps_passages():
    reader = FakeReader([{"text": "Join from the left echelon.", "doc": "sop-tanker", "score": 0.03}])
    embedder = FakeEmbedder()

    output = _run("  tanker join procedure  ", reader, embedder, k=3)

    assert output.passages[0].doc == "sop-tanker"
    assert output.passages[0].text == "Join from the left echelon."
    assert output.passages[0].score == pytest.approx(0.03)
    sql, args, timeout_s = reader.calls[0]
    assert sql == SEARCH_SQL
    assert args[0] == "tanker join procedure"
    assert args[1] == CANDIDATE_LIMIT
    assert args[2].startswith("[") and args[2].endswith("]")
    assert args[3] == RRF_K
    assert args[4] == 3
    assert timeout_s == 10
    assert embedder.texts == ["tanker join procedure"]


def test_sql_is_a_single_read():
    upper = SEARCH_SQL.upper()
    for keyword in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "GRANT", "COPY"):
        assert keyword not in upper
    assert "1.0 / ($4 + RANK)" in upper
    assert SEARCH_SQL.count(";") == 0


@pytest.mark.parametrize("query", ["", "   "])
def test_blank_query_never_reaches_the_index(query: str):
    reader = FakeReader()
    embedder = FakeEmbedder()

    with pytest.raises(ToolInputError, match="blank"):
        _run(query, reader, embedder)

    assert reader.calls == []
    assert embedder.texts == []


def test_bad_embedding_never_reaches_the_reader():
    reader = FakeReader()

    with pytest.raises(ToolInputError, match="3072"):
        _run("tanker join", reader, FakeEmbedder([0.2, 0.3]))

    assert reader.calls == []


def test_missing_index_is_a_clear_error():
    with pytest.raises(RuntimeError, match="doctrine index not configured"):
        _run("tanker join", None, FakeEmbedder())


def test_passages_can_be_searched_without_the_tool_wrapper():
    reader = FakeReader([{"text": "Procedure text.", "doc": "sop-1", "score": 1}])
    embedder = FakeEmbedder()

    rows = asyncio.run(search_passages(reader, embedder, "join", k=5, timeout_s=2))

    assert rows == [{"text": "Procedure text.", "doc": "sop-1", "score": 1.0}]
    assert reader.calls[0][2] == 2
