from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ScenarioEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    t: float
    type: str
    count: int | None = None
    near: str | None = None
    base: str | None = None
    reason: str | None = None


class BoundingBox(BaseModel):
    lat_min: float = 22.0
    lat_max: float = 33.0
    lon_min: float = 68.0
    lon_max: float = 78.0


class ScenarioConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    seed: int = 42
    horizon_hours: int = 72
    start: datetime = Field(default_factory=lambda: datetime.fromisoformat("2026-10-03T00:00:00+00:00"))
    bases: int = 7
    fleet: dict[str, int] = Field(default_factory=dict)
    crew_per_airframe: float = 2.0
    events: list[ScenarioEvent] = Field(default_factory=list)
    bbox: BoundingBox = Field(default_factory=BoundingBox)


def load_config(path: Path) -> ScenarioConfig:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"scenario config must be a mapping: {path}")
    return ScenarioConfig.model_validate(raw)
