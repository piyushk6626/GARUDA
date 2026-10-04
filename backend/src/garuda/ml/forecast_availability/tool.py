"""Forecast aircraft FMC probabilities without changing the public tool contract."""

import asyncio
from collections import defaultdict
from datetime import timedelta
import logging

from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.ml.forecast_availability.explain import explain
from garuda.ml.forecast_availability.features import features, require_aware
from garuda.ml.forecast_availability.predict import (
    BASELINE_VERSION,
    baseline,
    fleet_forecast,
    predict,
)
from garuda.ml.forecast_availability.schemas import (
    AircraftForecast,
    FleetForecast,
    ForecastAvailabilityInput,
    ForecastAvailabilityOutput,
)

logger = logging.getLogger(__name__)

SPEC = ToolSpec(
    name="forecast_availability",
    description="Probability each aircraft is FMC at T+24/48/72 h.",
    input_model=ForecastAvailabilityInput,
    output_model=ForecastAvailabilityOutput,
    side_effects="none",
    timeout_s=20,
)

# Compatibility fixture for the separate, still-unimplemented graph scaffold.
# The callable tool below never returns this fixture.
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
    """Fleet entries summarize the first requested horizon, keyed by base/type.

    The shared run_cpu executor is still a stub. Offload inference and exact
    fleet quantiles to a worker thread without changing that shared component.
    """
    return await asyncio.to_thread(_forecast, payload, ctx)


def _forecast(
    payload: ForecastAvailabilityInput, ctx: ToolContext
) -> ForecastAvailabilityOutput:
    horizons = list(dict.fromkeys(payload.horizons_h))
    if not horizons or any(horizon <= 0 for horizon in horizons):
        raise ToolInputError("horizons_h must contain at least one positive integer")
    require_aware(ctx.scenario_time, "scenario_time")
    try:
        ctx.scenario_time + timedelta(hours=max(horizons))
    except OverflowError as exc:
        raise ToolInputError("horizons_h exceeds the supported datetime range") from exc
    scenario = ctx.scenario.snapshot()
    plan = None
    if payload.include_planned_sorties_from is not None:
        try:
            plan = ctx.plans.get(payload.include_planned_sorties_from)
        except (KeyError, FileNotFoundError) as exc:
            raise ToolInputError(
                f"unknown plan_id: {payload.include_planned_sorties_from}"
            ) from exc
        if plan is None or plan.plan_id != payload.include_planned_sorties_from:
            raise ToolInputError(
                f"unknown plan_id: {payload.include_planned_sorties_from}"
            )
    aircraft = sorted(
        (
            airframe
            for airframe in scenario.aircraft
            if (payload.base_ids is None or airframe.base_id in payload.base_ids)
            and (payload.types is None or airframe.type in payload.types)
        ),
        key=lambda airframe: airframe.tail_no,
    )
    rows = features(scenario, aircraft, ctx.scenario_time, plan)
    artifact, frames = None, {}
    version = BASELINE_VERSION
    if rows:
        try:
            artifact = ctx.models.load(SPEC.name)
        except (FileNotFoundError, KeyError, NotImplementedError, OSError, ImportError):
            logger.info("Availability model unavailable; using %s", BASELINE_VERSION)
        if artifact is not None:
            try:
                probabilities, frames = predict(
                    artifact, rows, ctx.scenario_time, horizons
                )
                version = artifact.model_version
            except Exception:
                logger.warning(
                    "Availability model inference failed; using rule baseline",
                    exc_info=True,
                )
                artifact = None
        if artifact is None:
            probabilities = {
                horizon: [baseline(row, ctx.scenario_time, horizon) for row in rows]
                for horizon in horizons
            }
    else:
        probabilities = {horizon: [] for horizon in horizons}
    drivers = explain(artifact, frames, rows, ctx.scenario_time, horizons)
    per_aircraft = [
        AircraftForecast(
            tail=row.aircraft.tail_no,
            prob_by_horizon={
                str(horizon): probabilities[horizon][index] for horizon in horizons
            },
            top_drivers=drivers[index],
        )
        for index, row in enumerate(rows)
    ]
    groups: dict[str, list[float]] = defaultdict(list)
    for row, probability in zip(rows, probabilities[horizons[0]], strict=True):
        groups[f"{row.aircraft.base_id}/{row.aircraft.type}"].append(probability)
    return ForecastAvailabilityOutput(
        per_aircraft=per_aircraft,
        fleet={key: fleet_forecast(values) for key, values in sorted(groups.items())},
        model_version=version,
    )


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, forecast_availability)
