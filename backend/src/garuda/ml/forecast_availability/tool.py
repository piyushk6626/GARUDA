from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.forecast_availability.schemas import ForecastAvailabilityInput, ForecastAvailabilityOutput

SPEC = ToolSpec(
    name="forecast_availability",
    description="Probability each aircraft is FMC at T+24/48/72 h.",
    input_model=ForecastAvailabilityInput,
    output_model=ForecastAvailabilityOutput,
    side_effects="none",
    timeout_s=20,
)


async def forecast_availability(payload: ForecastAvailabilityInput, ctx: ToolContext) -> ForecastAvailabilityOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, forecast_availability)
