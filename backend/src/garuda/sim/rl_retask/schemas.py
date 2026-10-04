"""Input/output models from docs/03_optimization_layer/rl_retasking_agent.md."""

from pydantic import BaseModel

from garuda.contracts.events import Event
from garuda.contracts.plan import PlanDiff


class RlRetaskInput(BaseModel):
    plan_id: str
    event: Event
    deterministic: bool = True


class RlRetaskOutput(BaseModel):
    new_plan_id: str
    diff: PlanDiff
    policy_version: str
    decision_latency_ms: float
    compare_with_milp: dict[str, float] | None = None
