from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from garuda.context.ports import GraphReader, ModelStore, PlanStore, ScenarioSource
    from garuda.providers.protocols import Providers

SideEffect = Literal["none", "draft", "commit"]


class ToolSpec(BaseModel):
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    side_effects: SideEffect
    timeout_s: int


class ToolInputError(ValueError):
    """Actionable bad-input error the LLM can self-correct from."""


@dataclass(frozen=True)
class ToolContext:
    scenario: "ScenarioSource"
    plans: "PlanStore"
    models: "ModelStore"
    providers: "Providers"
    scenario_time: datetime
    seed: int = 42
    graph: "GraphReader | None" = None
