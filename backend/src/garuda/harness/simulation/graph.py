"""Run the simulation agent and pack its outputs for the main agent."""

from typing import Any

from garuda.contracts.tool import ToolContext
from garuda.harness.retrieval.graph import _question_from_state, run_loop
from garuda.harness.retrieval.schemas import ToolCallRecord
from garuda.harness.retrieval.state import RetrievalState
from garuda.harness.simulation.prompts import simulation_system_prompt
from garuda.harness.simulation.tools import SIM_TOOL_NAMES, default_sub_agents, make_run_call, model_tools
from garuda.harness.tasks.schemas import TaskFinishInput, TaskResult
from garuda.harness.tasks.session import TaskSession
from garuda.harness.tasks.tools import SubAgent

TASK_NAME = "simulation"


async def run_simulation(
    query: str,
    ctx: ToolContext,
    *,
    tool_budget: int = 8,
    model: Any | None = None,
    session: TaskSession | None = None,
    sub_agents: dict[str, SubAgent] | None = None,
) -> TaskResult:
    """Run the simulation tools the query needs and return their outputs."""
    if session is None:
        session = TaskSession()
    agents = default_sub_agents() if sub_agents is None else sub_agents
    state, session = await run_loop(
        query,
        ctx,
        tools=model_tools(),
        run_call=make_run_call(session, agents),
        system_prompt=simulation_system_prompt(ctx.scenario_time),
        tool_budget=tool_budget,
        model=model,
        session=session,
        finish_schema=TaskFinishInput,
    )
    return _simulation_result(query, state, session)


def _simulation_result(query: str, state: RetrievalState, session: TaskSession) -> TaskResult:
    records = [ToolCallRecord.model_validate(call) for call in state["calls"]]
    question = _question_from_state(state)
    raw_finish = state.get("finish_args")
    parsed = TaskFinishInput.model_validate(raw_finish) if raw_finish is not None else None
    return TaskResult(
        task=TASK_NAME,
        query=query,
        main_calls=[record for record in records if record.name in SIM_TOOL_NAMES],
        agent_calls=[record for record in records if record.name not in SIM_TOOL_NAMES],
        status=parsed.status if parsed else None,
        assumptions=list(parsed.assumptions) if parsed else [],
        note=parsed.note if parsed else "",
        question=question,
        session=session if question else None,
    )
