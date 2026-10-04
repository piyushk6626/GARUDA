"""Data tool `get_weather_along_route`. Backing: queries/weather.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.schemas.weather import WeatherRouteQuery, WeatherRouteResult, WeatherSegment
from garuda.tools.stubs import STUB_TIME, fixed

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

STUB_OUTPUT = GetWeatherAlongRouteOutput(
    segments=[
        WeatherSegment(
            time=STUB_TIME,
            lat=28.61,
            lon=77.21,
            flyability=0.8,
            ceiling_ft=3000.0,
            vis_km=8.0,
            wind_kts=12.0,
            wind_dir_deg=270.0,
            cb_flag=False,
        )
    ],
    worst_flyability=0.8,
    advisories=["skeleton"],
)


async def get_weather_along_route(
    payload: GetWeatherAlongRouteInput, ctx: ToolContext
) -> GetWeatherAlongRouteOutput:
    """Skeleton: same output for every input until the query is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)
