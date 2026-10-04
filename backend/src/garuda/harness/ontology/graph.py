"""Ontology sub-agent: the model picks templates, `query_ontology` runs them."""

from typing import Any

from garuda.contracts.tool import ToolContext
from garuda.harness.ontology.prompts import ontology_system_prompt
from garuda.harness.ontology.tools import model_tools, run_ontology_call
from garuda.harness.retrieval.graph import run_agent
from garuda.harness.retrieval.schemas import RetrievalResult
from garuda.harness.retrieval.session import RetrievalSession


async def query_graph(
    query: str,
    ctx: ToolContext,
    *,
    tool_budget: int = 4,
    model: Any | None = None,
    session: RetrievalSession | None = None,
) -> RetrievalResult:
    """Run the ontology templates the question needs and return their rows."""
    return await run_agent(
        query,
        ctx,
        tools=model_tools(),
        run_call=run_ontology_call,
        system_prompt=ontology_system_prompt(ctx.scenario_time),
        tool_budget=tool_budget,
        model=model,
        session=session,
    )
