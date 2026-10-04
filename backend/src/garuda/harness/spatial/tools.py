"""Bind spatial_query. The function and schema are used as-is."""

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from garuda.contracts.tool import ToolContext
from garuda.harness.retrieval.schemas import ToolCallRecord
from garuda.harness.retrieval.tools import (
    ASK_NAME,
    FINISH_NAME,
    AskQuestionInput,
    FinishInput,
    _schema_tool,
    _validation_message,
)
from garuda.tools.data.spatial_query import SPEC, spatial_query

SPATIAL_TOOL_NAME = SPEC.name


def model_tools() -> list[StructuredTool]:
    """The geometry tool plus the two control tools. No other data tools."""
    return [
        _schema_tool(SPEC.name, SPEC.description, SPEC.input_model),
        _schema_tool(
            ASK_NAME,
            "Ask the parent agent or a human one question when a place or radius is missing. Do not guess.",
            AskQuestionInput,
        ),
        _schema_tool(
            FINISH_NAME,
            "Call when the geometry queries for this question are done. Do not describe the results.",
            FinishInput,
        ),
    ]


async def run_spatial_call(name: str, args: dict[str, Any], ctx: ToolContext) -> ToolCallRecord:
    """Validate with the spatial schema, call the existing function, and record the output."""
    if name != SPEC.name:
        return ToolCallRecord(name=name, args=args, ok=False, error=f"unknown tool: {name}")
    try:
        payload = SPEC.input_model.model_validate(args)
    except ValidationError as exc:
        return ToolCallRecord(name=name, args=args, ok=False, error=_validation_message(exc))
    try:
        output = await spatial_query(payload, ctx)
    except Exception as exc:
        return ToolCallRecord(name=name, args=args, ok=False, error=str(exc))
    return ToolCallRecord(name=name, args=args, ok=True, data=output.model_dump(mode="json"))
