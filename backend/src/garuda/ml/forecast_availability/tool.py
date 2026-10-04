from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.forecast_availability.schemas import (
    AircraftForecast,
    FleetForecast,
    ForecastAvailabilityInput,
    ForecastAvailabilityOutput,
)
from garuda.tools.stubs import fixed

SPEC = ToolSpec(
    name="forecast_availability",
    description="Probability each aircraft is FMC at T+24/48/72 h.",
    input_model=ForecastAvailabilityInput,
    output_model=ForecastAvailabilityOutput,
    side_effects="none",
    timeout_s=20,
)

STUB_OUTPUT = ForecastAvailabilityOutput(
    per_aircraft=[
        AircraftForecast(
            tail="SU-A0123",
            prob_by_horizon={"24": 0.9, "48": 0.8, "72": 0.7},
            top_drivers=["hours_since_inspection"],
        )
    ],
    fleet={"B1": FleetForecast(mean=0.8, p10=0.6, p90=0.95)},
    model_version="skeleton-0",
)


async def forecast_availability(
    payload: ForecastAvailabilityInput, ctx: ToolContext
) -> ForecastAvailabilityOutput:
    """Skeleton: same output for every input until the model is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, forecast_availability)
