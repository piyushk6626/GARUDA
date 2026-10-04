"""Input/output models from docs/03_optimization_layer/lp_sortie_generation_rate.md."""

from datetime import datetime

from pydantic import BaseModel, Field


class ComputeSortieCapacityInput(BaseModel):
    base_ids: list[str] | None = None
    start: datetime
    horizon_hours: int = 24
    surge: bool = False


class ComputeSortieCapacityOutput(BaseModel):
    capacity: dict[str, list[int]]
    totals: dict[str, int]
    bottlenecks: dict[str, str]
    shadow_prices: dict[str, dict[str, float]] = Field(default_factory=dict)
