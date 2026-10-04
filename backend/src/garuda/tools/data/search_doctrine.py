"""Data tool `search_doctrine`. Backing: NEW queries/doctrine.py + ingest/doctrine.py. See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec


class SearchDoctrineInput(BaseModel):
    pass


class SearchDoctrineOutput(BaseModel):
    pass


SPEC = ToolSpec(
    name="search_doctrine",
    description="TODO(phase1): state when to use and not use.",
    input_model=SearchDoctrineInput,
    output_model=SearchDoctrineOutput,
    side_effects="none",
    timeout_s=10,
)


async def search_doctrine(payload: SearchDoctrineInput, ctx: ToolContext) -> SearchDoctrineOutput:
    raise NotImplementedError
