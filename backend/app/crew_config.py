"""Crew scenario/catalog parsing and reproducibility hashes."""
from __future__ import annotations
import hashlib, json, os
from datetime import datetime
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .schemas.crew import CrewRole, utc

class TypeCrewSpec(BaseModel):
    model_config=ConfigDict(frozen=True,extra="forbid")
    staffing: dict[CrewRole,int]
    qualifications: tuple[str,...]
    secondary_types: tuple[str,...]=()
    night_capable: bool=True
    aar_capable: bool=False
class CrewCatalog(BaseModel):
    model_config=ConfigDict(frozen=True,extra="forbid")
    types: dict[str,TypeCrewSpec]
    qualification_window_days:int=Field(default=90,gt=0)
    night_window_days:int=Field(default=90,gt=0)
    aar_window_days:int=Field(default=180,gt=0)
class CrewScenario(BaseModel):
    model_config=ConfigDict(frozen=True,extra="forbid")
    generator_version:str
    seed:int=42
    start_at:datetime
    horizon_hours:int=Field(default=72,gt=0)
    aircraft_scenario:str="config/scenarios/aircraft_demo.yaml"
    catalog:str="config/crew_types.yaml"
    total_crew:int=Field(default=300,gt=0)
    timezone:str="Asia/Kolkata"
    @field_validator("start_at")
    @classmethod
    def time(cls,v:datetime)->datetime:return utc(v)
def load_catalog(path:str|Path="config/crew_types.yaml")->CrewCatalog:
    return CrewCatalog.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
def load_scenario(path:str|Path,*,seed:int|None=None)->CrewScenario:
    raw=yaml.safe_load(Path(path).read_text(encoding="utf-8")); value=seed if seed is not None else int(os.getenv("GARUDA_SEED",raw.get("seed",42)))
    return CrewScenario.model_validate(raw).model_copy(update={"seed":value})
def stable_hash(value:object)->str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def scenario_hash(scenario:CrewScenario)->str:return stable_hash(scenario.model_dump(mode="json"))
