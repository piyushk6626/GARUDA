"""Bind the eight data-query tools. Their functions and schemas are used as-is."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.harness.retrieval.schemas import ToolCallRecord
from garuda.tools.data.get_aircraft_availability import SPEC as AIRCRAFT_SPEC
from garuda.tools.data.get_aircraft_availability import get_aircraft_availability
from garuda.tools.data.get_airspace import SPEC as AIRSPACE_SPEC
from garuda.tools.data.get_airspace import get_airspace
from garuda.tools.data.get_civil_traffic import SPEC as TRAFFIC_SPEC
from garuda.tools.data.get_civil_traffic import get_civil_traffic
from garuda.tools.data.get_crew_status import SPEC as CREW_SPEC
from garuda.tools.data.get_crew_status import get_crew_status
from garuda.tools.data.get_mission_requests import SPEC as MISSION_SPEC
from garuda.tools.data.get_mission_requests import get_mission_requests
from garuda.tools.data.get_threats_in_area import SPEC as THREAT_SPEC
from garuda.tools.data.get_threats_in_area import get_threats_in_area
from garuda.tools.data.get_weapon_stock import SPEC as WEAPON_SPEC
from garuda.tools.data.get_weapon_stock import get_weapon_stock
from garuda.tools.data.get_weather_along_route import SPEC as WEATHER_SPEC
from garuda.tools.data.get_weather_along_route import get_weather_along_route

FINISH_NAME = "finish"
ASK_NAME = "ask_question"
CONTROL_TOOL_NAMES = frozenset({FINISH_NAME, ASK_NAME})

ToolFn = Callable[..., Awaitable[BaseModel]]


class FinishInput(BaseModel):
    """Call when every data query needed for the question has been issued."""


class AskQuestionInput(BaseModel):
    """Ask the parent agent or a human when required context was not given."""

    question: str = Field(description="One concrete question. Do not guess the missing value.")


DATA_TOOLS: tuple[tuple[ToolSpec, ToolFn], ...] = (
    (AIRCRAFT_SPEC, get_aircraft_availability),
    (CREW_SPEC, get_crew_status),
    (WEAPON_SPEC, get_weapon_stock),
    (AIRSPACE_SPEC, get_airspace),
    (WEATHER_SPEC, get_weather_along_route),
    (THREAT_SPEC, get_threats_in_area),
    (MISSION_SPEC, get_mission_requests),
    (TRAFFIC_SPEC, get_civil_traffic),
)

TOOLS_BY_NAME: dict[str, tuple[ToolSpec, ToolFn]] = {spec.name: (spec, fn) for spec, fn in DATA_TOOLS}
DATA_TOOL_NAMES: tuple[str, ...] = tuple(TOOLS_BY_NAME)


def model_tools() -> list[StructuredTool]:
    """Schemas the model may call. The graph executes the real functions itself."""
    bound = [_schema_tool(spec.name, spec.description, spec.input_model) for spec, _fn in DATA_TOOLS]
    bound.append(
        _schema_tool(
            ASK_NAME,
            "Ask the parent agent or a human one question when required context is missing. Do not guess.",
            AskQuestionInput,
        )
    )
    bound.append(
        _schema_tool(
            FINISH_NAME,
            "Call when the data queries for this question are done. Do not describe the results.",
            FinishInput,
        )
    )
    return bound


def _schema_tool(name: str, description: str, args_schema: type[BaseModel]) -> StructuredTool:
    async def _unused(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(f"{name} is executed by the retrieval graph")

    return StructuredTool.from_function(
        coroutine=_unused,
        name=name,
        description=description,
        args_schema=args_schema,
    )


async def run_data_call(name: str, args: dict[str, Any], ctx: ToolContext) -> ToolCallRecord:
    """Validate with the tool schema, call the existing function, and record the output."""
    pair = TOOLS_BY_NAME.get(name)
    if pair is None:
        return ToolCallRecord(name=name, args=args, ok=False, error=f"unknown tool: {name}")
    spec, fn = pair
    try:
        payload = spec.input_model.model_validate(args)
    except ValidationError as exc:
        return ToolCallRecord(name=name, args=args, ok=False, error=_validation_message(exc))
    try:
        output = await fn(payload, ctx)
    except Exception as exc:
        return ToolCallRecord(name=name, args=args, ok=False, error=str(exc))
    return ToolCallRecord(name=name, args=args, ok=True, data=output.model_dump(mode="json"))


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    loc = ".".join(str(part) for part in first["loc"])
    message = str(first["msg"])
    if loc:
        return f"{loc}: {message}"
    return message
