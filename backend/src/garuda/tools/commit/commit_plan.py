"""commit_plan: side_effects=commit. Not exposed to the LLM; needs an HMAC approval token from the API."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec


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


async def commit_plan(payload: CommitPlanInput, ctx: ToolContext) -> CommitPlanOutput:
    raise NotImplementedError
