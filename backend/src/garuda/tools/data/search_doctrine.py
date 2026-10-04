"""Data tool `search_doctrine`. Backing: queries/doctrine.py. See docs/02_llm_harness/retrieval_and_context.md."""

from pydantic import BaseModel, Field

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.queries.doctrine import search_passages


class Passage(BaseModel):
    text: str
    doc: str
    score: float


class SearchDoctrineInput(BaseModel):
    query: str
    k: int = Field(5, ge=1, le=20)


class SearchDoctrineOutput(BaseModel):
    passages: list[Passage]


SPEC = ToolSpec(
    name="search_doctrine",
    description="Search doctrine and SOP passages. Use for procedure text; not for live aircraft or plan numbers.",
    input_model=SearchDoctrineInput,
    output_model=SearchDoctrineOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = SearchDoctrineOutput(
    passages=[Passage(text="Skeleton doctrine passage.", doc="sop-1", score=0.9)]
)


async def search_doctrine(payload: SearchDoctrineInput, ctx: ToolContext) -> SearchDoctrineOutput:
    """Top doctrine passages for a query. Full-text and vector ranks are fused in Postgres."""
    if ctx.doctrine is None or ctx.embedder is None:
        raise RuntimeError("doctrine index not configured")
    rows = await search_passages(
        ctx.doctrine,
        ctx.embedder,
        payload.query,
        k=payload.k,
        timeout_s=SPEC.timeout_s,
    )
    return SearchDoctrineOutput(passages=[Passage.model_validate(row) for row in rows])
