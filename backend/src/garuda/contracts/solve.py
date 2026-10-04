from datetime import datetime

from pydantic import BaseModel, Field


class SolveOptions(BaseModel):
    """Common solver knobs; every solver tool accepts these."""

    scenario_time: datetime
    horizon_hours: int = Field(default=72, gt=0)
    time_limit_s: int = Field(default=30, gt=0)
    seed: int = 42
