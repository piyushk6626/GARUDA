"""One model-facing tool per ontology template. Every call runs through `query_ontology`."""

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
from garuda.queries.ontology import TEMPLATES
from garuda.tools.data.query_ontology import QueryOntologyInput, query_ontology

TEMPLATE_NAMES: tuple[str, ...] = tuple(TEMPLATES)


def model_tools() -> list[StructuredTool]:
    """Template schemas the model may call. There is no tool that takes Cypher."""
    bound = [
        _schema_tool(name, definition.params.__doc__ or name, definition.params)
        for name, definition in TEMPLATES.items()
    ]
    bound.append(
        _schema_tool(
            ASK_NAME,
            "Ask the parent agent or a human one question when a required ID is missing. Do not guess.",
            AskQuestionInput,
        )
    )
    bound.append(
        _schema_tool(
            FINISH_NAME,
            "Call when the template queries for this question are done. Do not describe the rows.",
            FinishInput,
        )
    )
    return bound


async def run_ontology_call(name: str, args: dict[str, Any], ctx: ToolContext) -> ToolCallRecord:
    if name not in TEMPLATES:
        return ToolCallRecord(name=name, args=args, ok=False, error=f"unknown template: {name}")
    try:
        payload = QueryOntologyInput(template=name, params=args)
    except ValidationError as exc:
        return ToolCallRecord(name=name, args=args, ok=False, error=_validation_message(exc))
    try:
        output = await query_ontology(payload, ctx)
    except Exception as exc:
        return ToolCallRecord(name=name, args=args, ok=False, error=str(exc))
    return ToolCallRecord(name=name, args=args, ok=True, data=output.model_dump(mode="json"))
