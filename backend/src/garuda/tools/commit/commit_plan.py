"""commit_plan: side_effects=commit. Not exposed to the LLM; needs an HMAC approval token from the API."""

import time

from pydantic import BaseModel

from garuda.contracts.plan import PlanStatus
from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.tools.commit.approval import approval_secret, verify_approval


class CommitPlanInput(BaseModel):
    plan_id: str
    approval_token: str


class CommitPlanOutput(BaseModel):
    plan_id: str
    committed: bool
    approved_by: str


SPEC = ToolSpec(
    name="commit_plan",
    description="Commit an approved plan. Only callable by the Approval node.",
    input_model=CommitPlanInput,
    output_model=CommitPlanOutput,
    side_effects="commit",
    timeout_s=10,
)


STUB_OUTPUT = CommitPlanOutput(plan_id="plan-stub", committed=True, approved_by="stub")

COMMITTABLE: frozenset[PlanStatus] = frozenset({"APPROVED", "AWAITING_APPROVAL"})


async def commit_plan(payload: CommitPlanInput, ctx: ToolContext) -> CommitPlanOutput:
    claims = verify_approval(payload.approval_token, payload.plan_id, approval_secret(), int(time.time()))
    plan = ctx.plans.get(payload.plan_id)
    if plan.status == "COMMITTED":
        return CommitPlanOutput(plan_id=plan.plan_id, committed=True, approved_by=claims.approved_by)
    if plan.status not in COMMITTABLE:
        raise ToolInputError(
            f"plan {plan.plan_id} is {plan.status}; only {sorted(COMMITTABLE)} can be committed"
        )
    committed = plan.model_copy(update={"status": "COMMITTED"})
    plan_id = ctx.plans.put(committed) or committed.plan_id
    return CommitPlanOutput(plan_id=plan_id, committed=True, approved_by=claims.approved_by)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, commit_plan)
