"""Shared result envelope and explanation payloads."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SolverStatus = Literal["OPTIMAL", "FEASIBLE", "FEASIBLE_NOT_OPTIMAL", "INFEASIBLE", "ERROR"]


class Explanation(BaseModel):
    summary: str = ""
    binding_constraints: list[str] = Field(default_factory=list)
    kpis: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class Distribution(BaseModel):
    mean: float
    p5: float
    p50: float
    p95: float


class Sensitivity(BaseModel):
    factor: str
    effect: float


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    data_as_of: datetime
    explanation: str | None = None
    error: str | None = None
