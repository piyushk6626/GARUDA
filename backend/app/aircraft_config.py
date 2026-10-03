"""Scenario loading and canonical hashing for deterministic aircraft runs."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas.aircraft import canonical_type, utc


class BaseConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    base_id: str
    name: str
    icao: str = Field(pattern=r"^[A-Z]{4}$")
    role: str


class InjectionConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    at_hours: float = Field(ge=0)
    type: str
    count: int = Field(default=1, ge=1)


class AircraftScenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    generator_version: str
    seed: int = 42
    start_at: datetime
    horizon_hours: int = Field(gt=0)
    bases: tuple[BaseConfig, ...]
    fleet: dict[str, int]
    injections: tuple[InjectionConfig, ...] = ()

    @field_validator("start_at")
    @classmethod
    def _time(cls, value: datetime) -> datetime:
        return utc(value)

    @field_validator("fleet")
    @classmethod
    def _fleet(cls, values: dict[str, int]) -> dict[str, int]:
        output: dict[str, int] = {}
        for raw, count in values.items():
            kind = canonical_type(raw)
            if count <= 0:
                raise ValueError("fleet counts must be positive")
            output[kind] = output.get(kind, 0) + count
        if sum(output.values()) != 150:
            raise ValueError("aircraft demo fleet must contain exactly 150 aircraft")
        return output


def load_scenario(path: str | Path, *, seed: int | None = None) -> AircraftScenario:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    scenario = AircraftScenario.model_validate(raw)
    effective_seed = seed if seed is not None else int(os.getenv("GARUDA_SEED", scenario.seed))
    return scenario.model_copy(update={"seed": effective_seed})


def scenario_hash(scenario: AircraftScenario) -> str:
    document: dict[str, Any] = scenario.model_dump(mode="json")
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
