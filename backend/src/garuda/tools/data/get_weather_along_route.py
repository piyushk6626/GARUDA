"""Data tool `get_weather_along_route`. Backing: queries/weather.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.queries.weather import get_weather_along_route as query_weather_along_route
from garuda.schemas.weather import WeatherRouteQuery, WeatherRouteResult

GetWeatherAlongRouteInput = WeatherRouteQuery
GetWeatherAlongRouteOutput = WeatherRouteResult

SPEC = ToolSpec(
    name="get_weather_along_route",
    description="Weather and flyability for a route and time. Use before launch; not for a go/no-go probability model.",
    input_model=GetWeatherAlongRouteInput,
    output_model=GetWeatherAlongRouteOutput,
    side_effects="none",
    timeout_s=10,
)


async def get_weather_along_route(
    payload: GetWeatherAlongRouteInput, ctx: ToolContext
) -> GetWeatherAlongRouteOutput:
    """Sample the scenario forecast using the route query's timing and coverage rules."""
    return query_weather_along_route(ctx.scenario.snapshot().cells, payload)
