"""Scenario configuration and stable hashing for synthetic airspace."""
from __future__ import annotations
import hashlib, json, os
from datetime import datetime
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .schemas.airspace import ActivationWindow, utc

class Theatre(BaseModel):
    model_config=ConfigDict(frozen=True, extra="forbid")
    bbox: tuple[float,float,float,float]; metric_crs: str
    @field_validator("bbox")
    @classmethod
    def bounds(cls,v):
        if not (-180<=v[0]<v[2]<=180 and -90<=v[1]<v[3]<=90): raise ValueError("invalid theatre bbox")
        return v
class AirspaceScenario(BaseModel):
    model_config=ConfigDict(frozen=True, extra="forbid")
    generator_version: str; seed: int=42; start_at: datetime; horizon_hours: int=Field(gt=0)
    theatre: Theatre; border_file: str; border_sha256: str; adiz_buffer_km: float=Field(gt=0)
    tra_windows: tuple[ActivationWindow,...]=()
    @field_validator("start_at")
    @classmethod
    def time(cls,v): return utc(v)
def load_scenario(path:str|Path, *, seed:int|None=None)->AirspaceScenario:
    scenario=AirspaceScenario.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    return scenario.model_copy(update={"seed": seed if seed is not None else int(os.getenv("GARUDA_SEED",scenario.seed))})
def scenario_hash(scenario:AirspaceScenario)->str:
    return hashlib.sha256(json.dumps(scenario.model_dump(mode="json"),sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
