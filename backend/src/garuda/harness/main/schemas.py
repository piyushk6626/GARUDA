"""Work plan, step outcomes, and the main agent's return value.

The work plan lists which sub-agent runs each step. It is not the commit plan: it never
writes or commits anything, and its approval is separate from the commit approval token.
"""

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from garuda.harness.retrieval.schemas import ToolCallRecord
from garuda.harness.tasks.registry import TASKS

QUERY_AGENTS: tuple[str, ...] = ("retrieval", "spatial", "ontology")
SIMULATION_AGENT = "simulation"
AGENT_NAMES: tuple[str, ...] = (*QUERY_AGENTS, SIMULATION_AGENT, *TASKS)
MAX_STEPS = 12

Mode = Literal["agent", "plan"]
StepStatus = Literal["done", "partial", "blocked", "failed"]
MainStatus = Literal["answered", "awaiting_answer", "awaiting_approval", "blocked"]


class PlanStep(BaseModel):
    id: str = Field(min_length=1, description="Short unique step id, for example s1.")
    agent: str = Field(description="Sub-agent that runs this step. One of: " + ", ".join(AGENT_NAMES) + ".")
    objective: str = Field(min_length=1, description="What the sub-agent must do. One task, one main tool.")
    context: str = Field(
        default="",
        description="Ids, times, and facts the sub-agent needs, copied verbatim from the query, answers, or outputs.",
    )
    inputs_from: list[str] = Field(
        default_factory=list,
        description="Ids of earlier steps whose outputs this step needs.",
    )
    success_criteria: list[str] = Field(
        min_length=1,
        description="Checks that can be verified from this step's output, for example 'status done and plan_id present'.",
    )

    @field_validator("agent")
    @classmethod
    def _known_agent(cls, value: str) -> str:
        if value not in AGENT_NAMES:
            raise ValueError(f"unknown agent {value!r}; use one of {', '.join(AGENT_NAMES)}")
        return value


def plan_problem(steps: Sequence[PlanStep], done_ids: Sequence[str] = ()) -> str | None:
    """Why these steps cannot follow ``done_ids``, or None when they can."""
    if len(done_ids) + len(steps) > MAX_STEPS:
        return f"a plan has at most {MAX_STEPS} steps"
    seen = set(done_ids)
    for step in steps:
        if step.id in seen:
            return f"duplicate step id {step.id}"
        missing = [ref for ref in step.inputs_from if ref not in seen]
        if missing:
            return f"step {step.id} takes inputs_from {missing}, which are not earlier steps"
        seen.add(step.id)
    return None


class WorkPlanDraft(BaseModel):
    """Submit the work plan for human approval."""

    goal: str = Field(min_length=1, description="What the commander asked for, in one sentence.")
    steps: list[PlanStep] = Field(min_length=1, description="Steps in execution order.")

    @model_validator(mode="after")
    def _steps_are_ordered(self) -> "WorkPlanDraft":
        problem = plan_problem(self.steps)
        if problem:
            raise ValueError(problem)
        return self


class WorkPlan(WorkPlanDraft):
    version: int = 1


class StepOutcome(BaseModel):
    step_id: str
    agent: str
    status: StepStatus
    attempt: int = 1
    results: list[dict[str, Any]] = Field(default_factory=list)
    question: str | None = None
    error: str | None = None
    criteria_met: bool | None = None
    review_note: str = ""


class PlanApproval(BaseModel):
    approved: bool
    feedback: str = ""


class MainResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    query: str
    mode: Mode | None = None
    status: MainStatus
    question: str | None = None
    plan: WorkPlan | None = None
    outcomes: list[StepOutcome] = Field(default_factory=list)
    answer: str = ""
    calls: list[ToolCallRecord] = Field(default_factory=list)
    session: Any | None = Field(default=None, exclude=True)
