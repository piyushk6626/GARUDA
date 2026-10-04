"""Input/output models from docs/03_optimization_layer/threat_anomaly_detection.md."""

from typing import Literal

from pydantic import BaseModel

from garuda.contracts.geo import LatLon, PolygonModel


class AnomalyAlert(BaseModel):
    id: str
    type: str
    location: LatLon
    score: float
    evidence: list[str]
    suggested_threat_update: str


class DetectThreatAnomaliesInput(BaseModel):
    area: PolygonModel | None = None
    lookback_hours: int = 6
    sensitivity: Literal["low", "medium", "high"] = "medium"


class DetectThreatAnomaliesOutput(BaseModel):
    alerts: list[AnomalyAlert]
    baseline_period: str
