"""Ports the tools read through ToolContext. Implementations live in memory.py / postgres.py."""

from datetime import datetime
from typing import Protocol

from garuda.contracts.plan import PlanDraft
from garuda.schemas.mission import GeneratedScenario


class ScenarioSource(Protocol):
    def snapshot(self) -> GeneratedScenario: ...
    def base_ids(self) -> list[str]: ...
    def data_as_of(self) -> datetime: ...


class PlanStore(Protocol):
    def get(self, plan_id: str) -> PlanDraft: ...
    def put(self, plan: PlanDraft) -> str: ...


class ModelStore(Protocol):
    def load(self, name: str, version: str | None = None) -> object: ...
    def save(self, name: str, artifact: object, metadata: dict) -> str: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
