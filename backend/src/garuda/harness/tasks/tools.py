"""Tools a task agent may call: its optimizer tool, three query agents, ask, and finish."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.harness.retrieval.schemas import RetrievalResult, ToolCallRecord
from garuda.harness.retrieval.session import RetrievalSession
from garuda.harness.retrieval.tools import (
    ASK_NAME,
    FINISH_NAME,
    AskQuestionInput,
    _schema_tool,
    _validation_message,
)
from garuda.harness.tasks.schemas import TaskFinishInput
from garuda.harness.tasks.session import TaskSession

ASK_RETRIEVAL = "ask_retrieval_agent"
ASK_SPATIAL = "ask_spatial_agent"
ASK_ONTOLOGY = "ask_ontology_agent"
AGENT_TOOL_NAMES = frozenset({ASK_RETRIEVAL, ASK_SPATIAL, ASK_ONTOLOGY})

ToolFn = Callable[..., Awaitable[BaseModel]]
SubAgent = Callable[..., Awaitable[RetrievalResult]]


class SubAgentQueryInput(BaseModel):
    """One focused question for a query agent."""

    query: str = Field(min_length=1, description="One focused question. Do not bundle several asks.")


def model_tools(spec: ToolSpec) -> list[StructuredTool]:
    """The optimizer tool, the three query agents, and the two control tools."""
    return [
        _schema_tool(spec.name, spec.description, spec.input_model),
        _schema_tool(
            ASK_RETRIEVAL,
            "Ask the retrieval agent for live data or doctrine: aircraft, crew, weapons, airspace, "
            "weather, threats, missions, civil traffic, or SOP text. One focused question.",
            SubAgentQueryInput,
        ),
        _schema_tool(
            ASK_SPATIAL,
            "Ask the spatial agent for geometry only: distance, within a radius, nearest, intersects, "
            "or buffer. It does not fetch threats, weather, or routes. One focused question.",
            SubAgentQueryInput,
        ),
        _schema_tool(
            ASK_ONTOLOGY,
            "Ask the ontology agent for relationships and ids: qualified crew, missions for a target, "
            "assets at a base, threats covering a target, or sorties in a plan. One focused question.",
            SubAgentQueryInput,
        ),
        _schema_tool(
            ASK_NAME,
            "Ask the main agent or a human one question when a required input is missing. Do not guess.",
            AskQuestionInput,
        ),
        _schema_tool(
            FINISH_NAME,
            "Hand this task back. status is done, partial, or blocked. "
            "assumptions lists choices you made. note is a short reason with no numbers.",
            TaskFinishInput,
        ),
    ]


def default_sub_agents() -> dict[str, SubAgent]:
    """The three query agents, keyed by the tool name the task model calls."""
    from garuda.harness.ontology.graph import query_graph
    from garuda.harness.retrieval.graph import retrieve
    from garuda.harness.spatial.graph import query_geometry

    return {
        ASK_RETRIEVAL: retrieve,
        ASK_SPATIAL: query_geometry,
        ASK_ONTOLOGY: query_graph,
    }


def make_run_call(
    spec: ToolSpec,
    fn: ToolFn,
    session: TaskSession,
    sub_agents: dict[str, SubAgent],
) -> Callable[[str, dict[str, Any], ToolContext], Awaitable[ToolCallRecord]]:
    """Dispatch one model call to the optimizer tool or to a query agent."""

    async def run_call(name: str, args: dict[str, Any], ctx: ToolContext) -> ToolCallRecord:
        if name == spec.name:
            return await _run_main(spec, fn, args, ctx)
        agent = sub_agents.get(name)
        if agent is None:
            return ToolCallRecord(name=name, args=args, ok=False, error=f"unknown tool: {name}")
        return await _run_sub(name, args, ctx, session, agent)

    return run_call


async def _run_main(spec: ToolSpec, fn: ToolFn, args: dict[str, Any], ctx: ToolContext) -> ToolCallRecord:
    try:
        payload = spec.input_model.model_validate(args)
    except ValidationError as exc:
        return ToolCallRecord(name=spec.name, args=args, ok=False, error=_validation_message(exc))
    try:
        output = await fn(payload, ctx)
    except Exception as exc:
        return ToolCallRecord(name=spec.name, args=args, ok=False, error=str(exc))
    return ToolCallRecord(name=spec.name, args=args, ok=True, data=output.model_dump(mode="json"))


async def _run_sub(
    name: str,
    args: dict[str, Any],
    ctx: ToolContext,
    session: TaskSession,
    agent: SubAgent,
) -> ToolCallRecord:
    try:
        payload = SubAgentQueryInput.model_validate(args)
    except ValidationError as exc:
        return ToolCallRecord(name=name, args=args, ok=False, error=_validation_message(exc))
    child = session.children.setdefault(name, RetrievalSession())
    try:
        result = await agent(payload.query, ctx, session=child)
    except Exception as exc:
        return ToolCallRecord(name=name, args=args, ok=False, error=str(exc))
    return ToolCallRecord(name=name, args=args, ok=True, data=result.model_dump(mode="json"))
