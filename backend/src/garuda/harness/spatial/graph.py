"""Spatial sub-agent: the model picks a geometry op, `spatial_query` runs it."""

from typing import Any

from garuda.contracts.tool import ToolContext
from garuda.harness.retrieval.graph import run_agent
from garuda.harness.retrieval.schemas import RetrievalResult
from garuda.harness.retrieval.session import RetrievalSession
from garuda.harness.spatial.prompts import spatial_system_prompt
from garuda.harness.spatial.tools import model_tools, run_spatial_call


async def query_geometry(
    query: str,
    ctx: ToolContext,
    *,
    tool_budget: int = 4,
    model: Any | None = None,
    session: RetrievalSession | None = None,
) -> RetrievalResult:
    """Run the geometry operations the question needs and return their outputs."""
    return await run_agent(
        query,
        ctx,
        tools=model_tools(),
        run_call=run_spatial_call,
        system_prompt=spatial_system_prompt(ctx.scenario_time),
        tool_budget=tool_budget,
        model=model,
        session=session,
    )
