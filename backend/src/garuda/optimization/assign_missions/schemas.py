"""Input/output models. TODO(phase1): copy the signatures from docs/03_optimization_layer/milp_mission_aircraft_assignment.md."""

from pydantic import BaseModel


class AssignMissionsInput(BaseModel):
    pass


class AssignMissionsOutput(BaseModel):
    pass
