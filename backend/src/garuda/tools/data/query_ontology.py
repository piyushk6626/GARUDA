"""Data tool `query_ontology`. Backing: queries/ontology.py. See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.queries.ontology import OntologyTemplate, run_ontology_template

RowValue = str | int | float | bool | None


class QueryOntologyInput(BaseModel):
    template: OntologyTemplate
    params: dict[str, str | int | float | None]


class QueryOntologyOutput(BaseModel):
    rows: list[dict[str, RowValue]]


SPEC = ToolSpec(
    name="query_ontology",
    description=(
        "Run a named Cypher template: aircraft_with_qualified_crew, missions_for_target, assets_at_base, "
        "threats_covering_target, or plan_assignments. The model picks a template and parameters; "
        "it must not write Cypher."
    ),
    input_model=QueryOntologyInput,
    output_model=QueryOntologyOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = QueryOntologyOutput(rows=[{"tail_no": "SU-A0123", "crew_id": "C-001"}])


async def query_ontology(payload: QueryOntologyInput, ctx: ToolContext) -> QueryOntologyOutput:
    if ctx.graph is None:
        raise RuntimeError("graph reader not configured")
    rows = await run_ontology_template(ctx.graph, payload.template, payload.params, timeout_s=SPEC.timeout_s)
    return QueryOntologyOutput(rows=rows)
