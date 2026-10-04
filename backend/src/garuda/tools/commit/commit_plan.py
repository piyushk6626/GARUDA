"""commit_plan: side_effects=commit. Not exposed to the LLM; needs an HMAC approval token from the API."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.tools.stubs import fixed


class CommitPlanInput(BaseModel):
    plan_id: str
    approval_token: str


class CommitPlanOutput(BaseModel):
    plan_id: str
    committed: bool


SPEC = ToolSpec(
    name="commit_plan",
    description="Commit an approved plan. Only callable by the Approval node.",
    input_model=CommitPlanInput,
    output_model=CommitPlanOutput,
    side_effects="commit",
    timeout_s=10,
)


STUB_OUTPUT = CommitPlanOutput(plan_id="plan-stub", committed=True)


async def commit_plan(payload: CommitPlanInput, ctx: ToolContext) -> CommitPlanOutput:
    """Skeleton: same committed plan for every input until approval is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)
