"""Inference from versioned classifier artifacts, with an explicit rule fallback."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
from typing import Any

from garuda.ml.forecast_availability.features import (
    FEATURE_NAMES,
    AircraftFeatures,
    blocking_reason,
    horizon_features,
)
from garuda.ml.forecast_availability.schemas import FleetForecast

BASELINE_VERSION = "rule-baseline-1"


@dataclass(frozen=True)
class ForecastAvailabilityArtifact:
    """Artifact for ModelStore.load('forecast_availability').

    Models accept pandas frames and expose binary predict_proba (class 1 = FMC).
    They may include fitted preprocessing/calibration pipelines. Optional
    calibrators expose predict(probabilities), as isotonic regression does.
    Optional explainers return SHAP values in these feature coordinates.
    """

    model_version: str
    models: dict[int, Any]
    feature_names: tuple[str, ...] = FEATURE_NAMES
    calibrators: dict[int, Any] = field(default_factory=dict)
    explainers: dict[int, Any] = field(default_factory=dict)


def baseline(row: AircraftFeatures, at: datetime, horizon: int) -> float:
    if blocking_reason(row, at, horizon):
        return 0.0
    target = at + timedelta(hours=horizon)
    airframe = row.aircraft
    returned = (
        airframe.maint_return_eta is not None and airframe.maint_return_eta <= target
    )
    completed_mission = airframe.status == "IN_MISSION" and any(
        sortie.start <= at < sortie.end
        and sortie.end + timedelta(minutes=airframe.turnaround_min) <= target
        for sortie in row.sorties
    )
    if airframe.status != "FMC" and not returned and not completed_mission:
        return 0.0
    # The synthetic-data spec gives 3–5% degradation per sortie; use its midpoint.
    completed = sum(at < sortie.end <= target for sortie in row.sorties)
    return 0.96**completed


def predict(
    artifact: ForecastAvailabilityArtifact,
    rows: list[AircraftFeatures],
    at: datetime,
    horizons: list[int],
) -> tuple[dict[int, list[float]], dict[int, Any]]:
    import numpy as np
    import pandas as pd

    if not isinstance(artifact, ForecastAvailabilityArtifact):
        raise ValueError("expected ForecastAvailabilityArtifact")
    if (
        not isinstance(artifact.model_version, str)
        or not artifact.model_version.strip()
        or not artifact.feature_names
    ):
        raise ValueError("artifact needs a version and feature names")
    if len(set(artifact.feature_names)) != len(artifact.feature_names):
        raise ValueError("artifact feature names must be unique")
    unknown = set(artifact.feature_names) - set(FEATURE_NAMES)
    if unknown:
        raise ValueError(f"unsupported model features: {sorted(unknown)}")
    probabilities, frames = {}, {}
    for horizon in horizons:
        model = artifact.models[horizon]
        frame = pd.DataFrame(
            [horizon_features(row, at, horizon) for row in rows],
            columns=list(artifact.feature_names),
        )
        for name in frame.columns:
            if name not in {"type", "base", "current_status"}:
                frame[name] = pd.to_numeric(frame[name], errors="coerce")
        raw = np.asarray(model.predict_proba(frame), dtype=float)
        if raw.shape != (len(rows), 2):
            raise ValueError(
                "predict_proba must return one binary probability pair per aircraft"
            )
        if (
            not np.isfinite(raw).all()
            or (raw < 0).any()
            or (raw > 1).any()
            or not np.allclose(raw.sum(axis=1), 1.0)
        ):
            raise ValueError("classifier returned invalid probabilities")
        classes = list(getattr(model, "classes_", [0, 1]))
        if len(classes) != 2 or set(classes) != {0, 1}:
            raise ValueError("model classes must be 0 (not FMC) and 1 (FMC)")
        values = raw[:, classes.index(1)]
        if horizon in artifact.calibrators:
            values = np.asarray(
                artifact.calibrators[horizon].predict(values), dtype=float
            )
        if (
            values.shape != (len(rows),)
            or not np.isfinite(values).all()
            or (values < 0).any()
            or (values > 1).any()
        ):
            raise ValueError("calibrator returned invalid probabilities")
        probabilities[horizon] = [
            0.0 if blocking_reason(row, at, horizon) else float(value)
            for row, value in zip(rows, values, strict=True)
        ]
        frames[horizon] = frame
    return probabilities, frames


def fleet_forecast(probabilities: list[float]) -> FleetForecast:
    """Exact Poisson-binomial count quantiles, assuming independent airframes."""
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError("fleet probabilities must be finite and in [0, 1]")
    certain = sum(p == 1 for p in probabilities)
    mass = [1.0]
    for probability in probabilities:
        if probability in {0.0, 1.0}:
            continue
        updated = [0.0] * (len(mass) + 1)
        for count, value in enumerate(mass):
            updated[count] += value * (1 - probability)
            updated[count + 1] += value * probability
        mass = updated
    total = math.fsum(mass)

    def quantile(level: float) -> float:
        cumulative = 0.0
        for count, value in enumerate(mass):
            cumulative += value / total
            if cumulative >= level - 1e-12:
                return float(certain + count)
        return float(certain + len(mass) - 1)

    return FleetForecast(
        mean=math.fsum(probabilities), p10=quantile(0.1), p90=quantile(0.9)
    )
