"""query_ontology: fixed Cypher per template, strict params, read-only port."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, get_args

import pytest

from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.queries.ontology import ROW_LIMIT, TEMPLATES, OntologyTemplate
from garuda.tools.data.query_ontology import QueryOntologyInput, query_ontology

WHEN = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)


class FakeGraphReader:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    async def read(self, cypher: str, params: dict[str, Any], *, timeout_s: float) -> list[dict[str, Any]]:
        self.calls.append((cypher, params, timeout_s))
        return list(self.rows)


def _ctx(graph: Any = None) -> ToolContext:
    blank = SimpleNamespace()
    return ToolContext(scenario=blank, plans=blank, models=blank, providers=blank, scenario_time=WHEN, graph=graph)


def _run(template: str, params: dict[str, Any], reader: Any) -> Any:
    return asyncio.run(query_ontology(QueryOntologyInput(template=template, params=params), _ctx(reader)))


def test_templates_cover_the_literal():
    assert set(TEMPLATES) == set(get_args(OntologyTemplate))


@pytest.mark.parametrize(
    ("template", "params", "expected"),
    [
        (
            "aircraft_with_qualified_crew",
            {"base_id": "B1"},
            {"base_id": "B1", "aircraft_type": None, "status": "FMC", "max_fatigue": None},
        ),
        ("missions_for_target", {"target_id": "T1"}, {"target_id": "T1", "status": None}),
        ("assets_at_base", {"base_id": "B1", "asset_kind": "crew"}, {"base_id": "B1", "asset_kind": "crew"}),
        (
            "threats_covering_target",
            {"target_id": "T1", "min_confidence": 0.5},
            {"target_id": "T1", "min_confidence": 0.5},
        ),
        ("plan_assignments", {"plan_id": "P1"}, {"plan_id": "P1", "mission_id": None}),
    ],
)
def test_each_template_sends_its_fixed_cypher(template, params, expected):
    reader = FakeGraphReader([{"id": "x"}])

    output = _run(template, params, reader)

    assert output.rows == [{"id": "x"}]
    cypher, bound, timeout_s = reader.calls[0]
    assert cypher == TEMPLATES[template].cypher
    assert bound == {**expected, "limit": ROW_LIMIT}
    assert timeout_s == 10
    assert "LIMIT $limit" in cypher


@pytest.mark.parametrize(
    ("template", "params"),
    [
        ("aircraft_with_qualified_crew", {"base_id": "B1", "cypher": "MATCH (n) DETACH DELETE n"}),
        ("aircraft_with_qualified_crew", {}),
        ("aircraft_with_qualified_crew", {"base_id": "B1", "max_fatigue": 1.5}),
        ("assets_at_base", {"base_id": "B1", "asset_kind": "tankers"}),
        ("plan_assignments", {"plan_id": ""}),
    ],
)
def test_bad_params_never_reach_the_reader(template, params):
    reader = FakeGraphReader()

    with pytest.raises(ToolInputError, match=template):
        _run(template, params, reader)

    assert reader.calls == []


def test_unknown_template_is_rejected_by_the_input_model():
    with pytest.raises(ValueError):
        QueryOntologyInput(template="free_cypher", params={})


def test_missing_graph_reader_is_a_clear_error():
    with pytest.raises(RuntimeError, match="graph reader not configured"):
        _run("assets_at_base", {"base_id": "B1"}, None)


def test_no_template_writes():
    for definition in TEMPLATES.values():
        upper = definition.cypher.upper()
        for keyword in ("CREATE", "MERGE", "DELETE", "SET ", "REMOVE", "LOAD CSV"):
            assert keyword not in upper
