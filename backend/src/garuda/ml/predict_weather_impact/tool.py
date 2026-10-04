from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.predict_weather_impact.schemas import PredictWeatherImpactInput, PredictWeatherImpactOutput

SPEC = ToolSpec(
    name="predict_weather_impact",
    description="Probability weather causes no-go, degraded effect or diversion.",
    input_model=PredictWeatherImpactInput,
    output_model=PredictWeatherImpactOutput,
    side_effects="none",
    timeout_s=10,
)


async def predict_weather_impact(payload: PredictWeatherImpactInput, ctx: ToolContext) -> PredictWeatherImpactOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, predict_weather_impact)
