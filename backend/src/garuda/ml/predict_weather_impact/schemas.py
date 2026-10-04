"""Input/output models. TODO(phase1): copy the signatures from docs/03_optimization_layer/weather_impact_prediction.md."""

from pydantic import BaseModel


class PredictWeatherImpactInput(BaseModel):
    pass


class PredictWeatherImpactOutput(BaseModel):
    pass
