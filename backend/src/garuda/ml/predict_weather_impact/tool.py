import asyncio
from dataclasses import replace
from datetime import timedelta

from garuda.common import as_utc
from garuda.contracts.geo import TimeWindow
from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.predict_weather_impact.explain import explain
from garuda.ml.predict_weather_impact.features import (
    WeatherIndex,
    features,
    resolve_flight,
)
from garuda.ml.predict_weather_impact.predict import (
    MODEL_NAME,
    predict,
    validate_artifact,
)
from garuda.ml.predict_weather_impact.schemas import (
    AlternateBase,
    PredictWeatherImpactInput,
    PredictWeatherImpactOutput,
)
from garuda.queries.weather import _samples
from garuda.tools.stubs import STUB_TIME, STUB_TIME_END

SPEC = ToolSpec(
    name="predict_weather_impact",
    description="Probability weather causes no-go, degraded effect or diversion.",
    input_model=PredictWeatherImpactInput,
    output_model=PredictWeatherImpactOutput,
    side_effects="none",
    timeout_s=10,
)

# Compatibility fixture for the existing graph skeleton; inference never uses it.
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
    """Assess the sortie, hourly launch windows over 12h, and alternate recoveries."""
    # The shared process executor is still a stub. Keep synchronous port reads and
    # batched model inference off the event loop without changing shared infrastructure.
    return await asyncio.to_thread(_evaluate, payload, ctx)


def _evaluate(
    payload: PredictWeatherImpactInput, ctx: ToolContext
) -> PredictWeatherImpactOutput:
    snapshot = ctx.scenario.snapshot()
    flight = resolve_flight(payload, snapshot)
    try:
        artifact = ctx.models.load(MODEL_NAME)
    except (KeyError, FileNotFoundError, NotImplementedError) as error:
        raise RuntimeError(
            "a trained predict_weather_impact model is required; train and save it in ctx.models"
        ) from error
    if artifact is None:
        raise RuntimeError(
            "a trained predict_weather_impact model is required in ctx.models"
        )
    artifact = validate_artifact(artifact)
    index = WeatherIndex(snapshot.cells, ctx.scenario_time, artifact.climatology)
    rows = [features(snapshot, payload, index, flight)]
    start = as_utc(payload.start_time)
    # Each candidate represents one hourly window fully within the next 12h.
    windows = [
        TimeWindow(
            start=start + timedelta(hours=hour), end=start + timedelta(hours=hour + 1)
        )
        for hour in range(1, 12)
    ]
    if flight.mission is not None:
        windows = [
            window
            for window in windows
            if window.start >= as_utc(flight.mission.window_start)
            and window.end + timedelta(hours=flight.recovery_hours)
            <= as_utc(flight.mission.window_end)
        ]
    for window in windows:
        shifted = payload.model_copy(update={"start_time": window.start})
        rows.append(features(snapshot, shifted, index, flight))
    alternate_ids = []
    for base in sorted(snapshot.bases, key=lambda item: item.base_id):
        if base.base_id == (payload.recovery_base or payload.launch_base):
            continue
        diverted = payload.model_copy(update={"recovery_base": base.base_id})
        # A diversion replaces the final recovery leg, preserving the outbound route.
        route = (*flight.route[:-1], (base.lat, base.lon))
        transit_hours = _samples(list(route))[-1][2] / (flight.cruise_kts * 1.852)
        alternate_flight = replace(
            flight,
            route=route,
            recovery_hours=max(
                transit_hours, flight.mission.duration_min / 60 if flight.mission else 0
            ),
        )
        rows.append(features(snapshot, diverted, index, alternate_flight))
        alternate_ids.append(base.base_id)
    probabilities = predict(artifact, rows)
    p_go, p_degraded, p_no_go = map(float, probabilities[0])
    # Missing forecasts must never make a suggested window or diversion look better.
    better = [
        (float(probabilities[i, 0]), window)
        for i, window in enumerate(windows, start=1)
        if rows[i].values["missing_fraction"] == 0 and probabilities[i, 0] > p_go + 1e-6
    ]
    better.sort(key=lambda item: (-item[0], item[1].start))
    offset = 1 + len(windows)
    alternates = [
        AlternateBase(base_id=base_id, p_go=float(probabilities[offset + i, 0]))
        for i, base_id in enumerate(alternate_ids)
        if rows[offset + i].values["missing_fraction"] == 0
        and probabilities[offset + i, 0] > p_go + 1e-6
    ]
    alternates.sort(key=lambda item: (-item.p_go, item.base_id))
    return PredictWeatherImpactOutput(
        p_go=p_go,
        p_degraded=p_degraded,
        p_no_go=p_no_go,
        limiting_factors=explain(rows[0]),
        better_windows=[window for _, window in better[:3]],
        alternates=alternates,
    )


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, predict_weather_impact)
