"""Input/output models from docs/03_optimization_layer/weapon_target_assignment.md."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class WeaponAllocation(BaseModel):
    target_id: str
    weapon_code: str
    count: int
    base_id: str


class AssignWeaponsToTargetsInput(BaseModel):
    target_ids: list[str]
    base_ids: list[str] | None = None
    method: Literal["greedy", "milp"] = "greedy"
    min_destroy_prob: dict[str, float] = Field(default_factory=dict)
    scenario_time: datetime


class AssignWeaponsToTargetsOutput(BaseModel):
    allocations: list[WeaponAllocation]
    expected_destroyed_value: float
    kill_prob_by_target: dict[str, float]
    stock_remaining: dict[str, int]
