"""Data tool `query_ontology`. Backing: NEW queries/ontology.py. See docs/02_llm_harness/data_query_tools.md."""

from typing import Literal

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.tools.stubs import fixed

OntologyTemplate = Literal[
    "aircraft_with_qualified_crew",
    "missions_for_target",
    "assets_at_base",
    "threats_covering_target",
    "plan_assignments",
]


class QueryOntologyInput(BaseModel):
    template: OntologyTemplate
    params: dict[str, str | int | float]


class QueryOntologyOutput(BaseModel):
    rows: list[dict[str, str | int | float]]


SPEC = ToolSpec(
    name="query_ontology",
    description="Run a named Cypher template. Use for relations; the model must not write free Cypher.",
    input_model=QueryOntologyInput,
    output_model=QueryOntologyOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = QueryOntologyOutput(rows=[{"tail_no": "SU-A0123", "crew_id": "C-001"}])


async def query_ontology(payload: QueryOntologyInput, ctx: ToolContext) -> QueryOntologyOutput:
    """Skeleton: same output for every input until the query is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)
