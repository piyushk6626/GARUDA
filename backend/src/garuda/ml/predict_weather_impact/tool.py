from garuda.contracts.geo import TimeWindow
from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.predict_weather_impact.schemas import (
    AlternateBase,
    PredictWeatherImpactInput,
    PredictWeatherImpactOutput,
)
from garuda.tools.stubs import STUB_TIME, STUB_TIME_END, fixed

SPEC = ToolSpec(
    name="predict_weather_impact",
    description="Probability weather causes no-go, degraded effect or diversion.",
    input_model=PredictWeatherImpactInput,
    output_model=PredictWeatherImpactOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = PredictWeatherImpactOutput(
    p_go=0.7,
    p_degraded=0.2,
    p_no_go=0.1,
    limiting_factors=["ceiling"],
    better_windows=[TimeWindow(start=STUB_TIME, end=STUB_TIME_END)],
    alternates=[AlternateBase(base_id="B2", p_go=0.85)],
)


async def predict_weather_impact(
    payload: PredictWeatherImpactInput, ctx: ToolContext
) -> PredictWeatherImpactOutput:
    """Skeleton: same output for every input until the model is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, predict_weather_impact)
