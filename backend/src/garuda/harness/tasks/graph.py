"""Run one optimizer task agent and pack its outputs for the main agent."""

from typing import Any

from garuda.contracts.tool import ToolContext
from garuda.harness.retrieval.graph import _question_from_state, run_loop
from garuda.harness.retrieval.schemas import ToolCallRecord
from garuda.harness.retrieval.state import RetrievalState
from garuda.harness.tasks.registry import TASKS
from garuda.harness.tasks.schemas import TaskFinishInput, TaskResult
from garuda.harness.tasks.session import TaskSession
from garuda.harness.tasks.tools import SubAgent, default_sub_agents, make_run_call, model_tools


async def run_task(
    task: str,
    query: str,
    ctx: ToolContext,
    *,
    tool_budget: int | None = None,
    model: Any | None = None,
    session: TaskSession | None = None,
    sub_agents: dict[str, SubAgent] | None = None,
) -> TaskResult:
    """Run the named task agent. An unknown task raises before any model call."""
    definition = TASKS.get(task)
    if definition is None:
        raise ValueError(f"unknown task: {task}")
    if session is None:
        session = TaskSession()
    agents = default_sub_agents() if sub_agents is None else sub_agents
    budget = definition.tool_budget if tool_budget is None else tool_budget
    state, session = await run_loop(
        query,
        ctx,
        tools=model_tools(definition.spec),
        run_call=make_run_call(definition.spec, definition.fn, session, agents),
        system_prompt=definition.prompt(ctx.scenario_time),
        tool_budget=budget,
        model=model,
        session=session,
        finish_schema=TaskFinishInput,
    )
    return _task_result(definition.spec.name, query, state, session)


def _task_result(task: str, query: str, state: RetrievalState, session: TaskSession) -> TaskResult:
    records = [ToolCallRecord.model_validate(call) for call in state["calls"]]
    question = _question_from_state(state)
    status = None
    assumptions: list[str] = []
    note = ""
    raw_finish = state.get("finish_args")
    if raw_finish is not None:
        parsed = TaskFinishInput.model_validate(raw_finish)
        status = parsed.status
        assumptions = list(parsed.assumptions)
        note = parsed.note
    return TaskResult(
        task=task,
        query=query,
        main_calls=[record for record in records if record.name == task],
        agent_calls=[record for record in records if record.name != task],
        status=status,
        assumptions=assumptions,
        note=note,
        question=question,
        session=session if question else None,
    )
