"""Input/output models. TODO(phase1): copy the signatures from docs/03_optimization_layer/mission_success_probability.md."""

from pydantic import BaseModel


class PredictMissionSuccessInput(BaseModel):
    pass


class PredictMissionSuccessOutput(BaseModel):
    pass
