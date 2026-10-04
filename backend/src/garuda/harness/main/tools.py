"""Tools the main agent may call in each phase. The phase machine executes them itself."""

from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from garuda.harness.main.schemas import PlanStep, WorkPlanDraft
from garuda.harness.retrieval.tools import ASK_NAME, AskQuestionInput, _schema_tool
from garuda.harness.tasks.tools import ASK_ONTOLOGY, ASK_RETRIEVAL, ASK_SPATIAL, SubAgentQueryInput

CHOOSE_MODE = "choose_mode"
RESPOND = "respond"
SWITCH_TO_PLAN = "switch_to_plan"
PROPOSE_PLAN = "propose_plan"
ANSWER_SUBAGENT = "answer_subagent"
ASK_HUMAN = "ask_human"
CONTINUE_PLAN = "continue_plan"
REVISE_PLAN = "revise_plan"
RETRY_STEP = "retry_step"
ABORT = "abort"


class ChooseModeInput(BaseModel):
    """Pick how to handle the request."""

    mode: Literal["agent", "plan"] = Field(description="agent for a quick lookup, plan for multi-step work.")
    reason: str = Field(default="", description="One short reason.")


class RespondInput(BaseModel):
    """Give the final answer to the commander."""

    answer: str = Field(min_length=1, description="The answer. Every number is quoted from a tool output.")


class SwitchToPlanInput(BaseModel):
    """Hand the request to plan mode."""

    reason: str = Field(min_length=1, description="Why a quick lookup is not enough.")


class AnswerSubagentInput(BaseModel):
    """Answer the sub-agent's question from what is already known."""

    answer: str = Field(min_length=1, description="The answer, taken from the query, an answer, or an output.")


class AskHumanInput(BaseModel):
    """Ask the commander when nothing known answers the sub-agent's question."""

    question: str = Field(min_length=1, description="One concrete question for the commander.")


class StepEdit(BaseModel):
    step_id: str = Field(description="A step that has not run yet.")
    context: str | None = Field(default=None, description="Replacement context. Ids copied verbatim.")
    success_criteria: list[str] | None = Field(default=None, description="Replacement success criteria.")


class ContinuePlanInput(BaseModel):
    """Move on to the next step. Only context and success criteria of later steps may change."""

    criteria_met: bool = Field(description="Whether the finished step met its success criteria.")
    note: str = Field(default="", description="One short review note.")
    step_edits: list[StepEdit] = Field(default_factory=list, description="Edits to steps that have not run.")


class RevisePlanInput(BaseModel):
    """Replace every step that has not run. Adding, removing, reordering, or reassigning needs approval."""

    criteria_met: bool = Field(description="Whether the finished step met its success criteria.")
    reason: str = Field(min_length=1, description="Why the plan must change.")
    remaining_steps: list[PlanStep] = Field(description="The new steps after the finished one, in order.")


class RetryStepInput(BaseModel):
    """Run the finished step once more with extra context."""

    criteria_met: bool = Field(default=False, description="Whether the step met its success criteria.")
    reason: str = Field(min_length=1, description="What went wrong.")
    context_patch: str = Field(min_length=1, description="Context added to the step for the retry.")


class AbortInput(BaseModel):
    """Stop executing the plan and go to the final answer."""

    criteria_met: bool = Field(default=False, description="Whether the finished step met its success criteria.")
    reason: str = Field(min_length=1, description="Why the plan cannot continue.")


QUERY_TOOL_DESCRIPTIONS: dict[str, str] = {
    ASK_RETRIEVAL: "Ask the retrieval agent for live data or doctrine: aircraft, crew, weapons, airspace, "
    "weather, threats, missions, civil traffic, or SOP text. One focused question.",
    ASK_SPATIAL: "Ask the spatial agent for geometry only: distance, within a radius, nearest, intersects, "
    "or buffer. One focused question.",
    ASK_ONTOLOGY: "Ask the ontology agent for relationships and ids: qualified crew, missions for a target, "
    "assets at a base, threats covering a target, or sorties in a plan. One focused question.",
}

ROUTER_TERMINALS: dict[str, type[BaseModel]] = {CHOOSE_MODE: ChooseModeInput}
AGENT_TERMINALS: dict[str, type[BaseModel]] = {RESPOND: RespondInput, SWITCH_TO_PLAN: SwitchToPlanInput}
DISCOVER_TERMINALS: dict[str, type[BaseModel]] = {PROPOSE_PLAN: WorkPlanDraft}
CLARIFY_TERMINALS: dict[str, type[BaseModel]] = {ANSWER_SUBAGENT: AnswerSubagentInput, ASK_HUMAN: AskHumanInput}
REVIEW_TERMINALS: dict[str, type[BaseModel]] = {
    CONTINUE_PLAN: ContinuePlanInput,
    REVISE_PLAN: RevisePlanInput,
    RETRY_STEP: RetryStepInput,
    ABORT: AbortInput,
}
RESPOND_TERMINALS: dict[str, type[BaseModel]] = {RESPOND: RespondInput}


def terminal_tools(terminals: dict[str, type[BaseModel]]) -> list[StructuredTool]:
    return [_schema_tool(name, schema.__doc__ or name, schema) for name, schema in terminals.items()]


def query_tools() -> list[StructuredTool]:
    """The three query agents, plus ask_question to the commander."""
    tools = [_schema_tool(name, text, SubAgentQueryInput) for name, text in QUERY_TOOL_DESCRIPTIONS.items()]
    tools.append(
        _schema_tool(
            ASK_NAME,
            "Ask the commander one question when required context is missing. Do not guess.",
            AskQuestionInput,
        )
    )
    return tools
