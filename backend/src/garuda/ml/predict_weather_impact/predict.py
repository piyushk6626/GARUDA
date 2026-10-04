"""Strict inference against a calibrated weather-impact artifact."""

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from garuda.ml.predict_weather_impact.features import FEATURE_NAMES, WeatherFeatures
from garuda.schemas.weather import WeatherCell

MODEL_NAME = "predict_weather_impact"
CLASSES = ("GO", "DEGRADED", "NO_GO")


@dataclass(frozen=True)
class WeatherImpactArtifact:
    estimator: Any
    climatology: WeatherCell
    feature_names: tuple[str, ...] = FEATURE_NAMES
    metrics: dict[str, float] = field(default_factory=dict)


def validate_artifact(artifact: object) -> WeatherImpactArtifact:
    if not isinstance(artifact, WeatherImpactArtifact):
        raise TypeError(
            "weather-impact model must be a WeatherImpactArtifact produced by train(ctx)"
        )
    if tuple(artifact.feature_names) != FEATURE_NAMES:
        raise ValueError(
            "weather-impact model has incompatible feature names/order; retrain it"
        )
    classes = tuple(str(value) for value in getattr(artifact.estimator, "classes_", ()))
    if len(classes) != 3 or set(classes) != set(CLASSES):
        raise ValueError(
            "weather-impact model must be trained on GO, DEGRADED, and NO_GO"
        )
    if not callable(getattr(artifact.estimator, "predict_proba", None)):
        raise TypeError("weather-impact model does not support predict_proba")
    if not isinstance(artifact.climatology, WeatherCell):
        raise TypeError("weather-impact model is missing its training climatology")
    return artifact


def predict(artifact: WeatherImpactArtifact, rows: list[WeatherFeatures]) -> np.ndarray:
    """Return columns in GO/DEGRADED/NO_GO order, regardless of estimator class order."""
    validate_artifact(artifact)
    if not rows:
        return np.empty((0, 3))
    matrix = np.asarray([row.vector() for row in rows], dtype=float)
    if not np.isfinite(matrix).all():
        raise ValueError("weather-impact features contain non-finite values")
    probabilities = np.asarray(artifact.estimator.predict_proba(matrix), dtype=float)
    if probabilities.shape != (len(rows), 3) or not np.isfinite(probabilities).all():
        raise ValueError("weather-impact model returned invalid probabilities")
    if (probabilities < 0).any() or (probabilities > 1).any():
        raise ValueError("weather-impact model probabilities must be in [0, 1]")
    totals = probabilities.sum(axis=1, keepdims=True)
    if not np.allclose(totals, 1.0, atol=1e-6, rtol=0):
        raise ValueError("weather-impact model probabilities must sum to one")
    classes = [str(value) for value in artifact.estimator.classes_]
    return (probabilities / totals)[:, [classes.index(name) for name in CLASSES]]
