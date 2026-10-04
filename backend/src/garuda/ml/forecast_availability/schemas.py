"""Input/output models. TODO(phase1): copy the signatures from docs/03_optimization_layer/aircraft_availability_forecast.md."""

from pydantic import BaseModel


class ForecastAvailabilityInput(BaseModel):
    pass


class ForecastAvailabilityOutput(BaseModel):
    pass
