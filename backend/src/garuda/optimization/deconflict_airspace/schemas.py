"""Input/output models. TODO(phase1): copy the signatures from docs/03_optimization_layer/cpsat_airspace_deconfliction.md."""

from pydantic import BaseModel


class DeconflictAirspaceInput(BaseModel):
    pass


class DeconflictAirspaceOutput(BaseModel):
    pass
