from pydantic import BaseModel

from garuda.contracts.plan import PlanDraft
from garuda.contracts.tool import ToolContext


class Violation(BaseModel):
    rule: str
    entity_id: str
    detail: str


def validate_plan(plan: PlanDraft, ctx: ToolContext) -> list[Violation]:
    """Run every rule in rules.py independently of the solver. TODO(phase1)."""
    raise NotImplementedError
