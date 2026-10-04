"""Data tool `query_ontology`. Backing: NEW queries/ontology.py. See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec


class QueryOntologyInput(BaseModel):
    pass


class QueryOntologyOutput(BaseModel):
    pass


SPEC = ToolSpec(
    name="query_ontology",
    description="TODO(phase1): state when to use and not use.",
    input_model=QueryOntologyInput,
    output_model=QueryOntologyOutput,
    side_effects="none",
    timeout_s=10,
)


async def query_ontology(payload: QueryOntologyInput, ctx: ToolContext) -> QueryOntologyOutput:
    raise NotImplementedError
