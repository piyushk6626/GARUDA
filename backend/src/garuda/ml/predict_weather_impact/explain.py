"""Observed weather constraints and confidence flags (not causal model attributions)."""

from garuda.ml.predict_weather_impact.features import WeatherFeatures


def explain(row: WeatherFeatures) -> list[str]:
    return list(row.limiting_factors)
