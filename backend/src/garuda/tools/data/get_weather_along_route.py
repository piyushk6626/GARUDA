"""Data tool `get_weather_along_route`. Backing: queries/weather.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec


class GetWeatherAlongRouteInput(BaseModel):
    pass


class GetWeatherAlongRouteOutput(BaseModel):
    pass


SPEC = ToolSpec(
    name="get_weather_along_route",
    description="TODO(phase1): state when to use and not use.",
    input_model=GetWeatherAlongRouteInput,
    output_model=GetWeatherAlongRouteOutput,
    side_effects="none",
    timeout_s=10,
)


async def get_weather_along_route(payload: GetWeatherAlongRouteInput, ctx: ToolContext) -> GetWeatherAlongRouteOutput:
    raise NotImplementedError
