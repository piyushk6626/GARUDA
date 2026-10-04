"""Input/output models. TODO(phase1): copy the signatures from docs/03_optimization_layer/threat_anomaly_detection.md."""

from pydantic import BaseModel


class DetectThreatAnomaliesInput(BaseModel):
    pass


class DetectThreatAnomaliesOutput(BaseModel):
    pass
