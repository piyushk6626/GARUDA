"""Main agent: routes a request to agent mode or plan mode and drives the plan step by step.

Each phase is one tool loop. A phase that needs the commander returns a MainResult that
carries the session; ``resume_main`` continues from the same phase.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, ValidationError

from garuda.contracts.tool import ToolContext
from garuda.harness.main.agents import (
    QUERY_TOOL_AGENTS,
    Delegate,
    build_step_query,
    compact_json,
    default_agents,
    delegate,
    new_step_session,
)
from garuda.harness.main.prompts import PromptPhase, main_system_prompt
from garuda.harness.main.schemas import (
    MainResult,
    MainStatus,
    PlanApproval,
    PlanStep,
    StepOutcome,
    WorkPlan,
    WorkPlanDraft,
    plan_problem,
)
from garuda.harness.main.session import MainSession
from garuda.harness.main.tools import (
    ABORT,
    AGENT_TERMINALS,
    ANSWER_SUBAGENT,
    ASK_HUMAN,
    CLARIFY_TERMINALS,
    CONTINUE_PLAN,
    DISCOVER_TERMINALS,
    PROPOSE_PLAN,
    RESPOND,
    RESPOND_TERMINALS,
    RETRY_STEP,
    REVIEW_TERMINALS,
    REVISE_PLAN,
    ROUTER_TERMINALS,
    SWITCH_TO_PLAN,
    ContinuePlanInput,
    RetryStepInput,
    RevisePlanInput,
    query_tools,
    terminal_tools,
)
from garuda.harness.retrieval.graph import _as_call
from garuda.harness.retrieval.schemas import ToolCallRecord
from garuda.harness.retrieval.tools import ASK_NAME, _validation_message
from garuda.harness.tasks.tools import _run_sub

AGENT_QUERY_BUDGET = 4
DISCOVER_QUERY_BUDGET = 10
MAX_CLARIFY_ROUNDS = 3
MAX_RETRIES = 1
DECISION_TURNS = 3

Check = Callable[[str, dict[str, Any]], str | None]


@dataclass
class LoopEnd:
    """How a tool loop stopped: a terminal tool, ask_question, or "" when the turns ran out."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)


async def run_main(
    query: str,
    ctx: ToolContext,
    *,
    mode: Literal["auto", "agent", "plan"] = "auto",
    model: Any | None = None,
    sub_agents: dict[str, Delegate] | None = None,
) -> MainResult:
    """Handle one commander request. ``mode`` other than auto skips the router."""
    if model is None:
        from garuda.harness.llm import chat_model

        model = chat_model()
    agents = default_agents() if sub_agents is None else sub_agents
    session = MainSession(query, model=model, agents=agents)
    if mode != "auto":
        _enter_mode(session, ctx, mode)
    return await _advance(session, ctx)


async def resume_main(
    session: MainSession,
    ctx: ToolContext,
    *,
    answer: str | None = None,
    approval: PlanApproval | None = None,
    model: Any | None = None,
) -> MainResult:
    """Continue a session that is waiting for an answer or for plan approval."""
    if not session.open:
        raise ValueError("session is closed")
    if session.awaiting is None:
        raise ValueError("session is not waiting for input")
    if session.awaiting == "answer" and answer is None:
        raise ValueError("session is waiting for an answer")
    if session.awaiting == "approval" and approval is None:
        raise ValueError("session is waiting for plan approval")
    if model is not None:
        session.model = model
    session.turn_calls = []
    if session.awaiting == "answer":
        _deliver_answer(session, answer or "")
    else:
        _apply_approval(session, approval)
    session.awaiting = None
    return await _advance(session, ctx)


async def _advance(session: MainSession, ctx: ToolContext) -> MainResult:
    phases = {
        "route": _route,
        "agent": _agent_mode,
        "discover": _discover,
        "execute": _execute,
        "clarify": _clarify,
        "review": _review,
        "respond": _respond,
    }
    while True:
        result = await phases[session.phase](session, ctx)
        if result is not None:
            return result


# Phases. Each returns a MainResult to pause or finish, or None to continue.


async def _route(session: MainSession, ctx: ToolContext) -> MainResult | None:
    messages: list[AnyMessage] = [
        SystemMessage(content=main_system_prompt(ctx.scenario_time, "route")),
        HumanMessage(content=session.query),
    ]
    end = await _tool_loop(session, ctx, messages, ROUTER_TERMINALS, max_turns=2)
    mode = end.args.get("mode") if end.name else None
    _enter_mode(session, ctx, mode if mode in ("agent", "plan") else "plan")
    return None


async def _agent_mode(session: MainSession, ctx: ToolContext) -> MainResult | None:
    end = await _tool_loop(
        session,
        ctx,
        session.messages,
        AGENT_TERMINALS,
        query_budget=AGENT_QUERY_BUDGET,
        max_turns=AGENT_QUERY_BUDGET + 2,
        allow_ask=True,
    )
    if end.name == RESPOND:
        return _finish(session, "answered", answer=end.args["answer"])
    if end.name == SWITCH_TO_PLAN:
        session.mode = "plan"
        session.phase = "discover"
        session.messages[0] = SystemMessage(content=main_system_prompt(ctx.scenario_time, "discover"))
        session.messages.append(
            HumanMessage(content=f"Switched to plan mode: {end.args['reason']}. Discover what is missing, then propose_plan.")
        )
        return None
    if end.name == ASK_NAME:
        return _await_answer(session, end.args["question"])
    return _finish(session, "blocked", answer="Agent mode ended without an answer.")


async def _discover(session: MainSession, ctx: ToolContext) -> MainResult | None:
    end = await _tool_loop(
        session,
        ctx,
        session.messages,
        DISCOVER_TERMINALS,
        query_budget=DISCOVER_QUERY_BUDGET,
        max_turns=DISCOVER_QUERY_BUDGET + 4,
        allow_ask=True,
    )
    if end.name == PROPOSE_PLAN:
        draft = WorkPlanDraft.model_validate(end.args)
        version = session.plan_history[-1].version + 1 if session.plan_history else 1
        if session.proposed is not None:
            version = max(version, session.proposed.version + 1)
        session.proposed = WorkPlan(goal=draft.goal, steps=draft.steps, version=version)
        session.approval_kind = "initial"
        return _await_approval(session)
    if end.name == ASK_NAME:
        return _await_answer(session, end.args["question"])
    return _finish(session, "blocked", answer="Discovery ended without a work plan.")


async def _execute(session: MainSession, ctx: ToolContext) -> MainResult | None:
    plan = _plan(session)
    if session.step_index >= len(plan.steps):
        session.phase = "respond"
        return None
    step = plan.steps[session.step_index]
    if session.step_reply is not None:
        query = session.step_reply
        session.step_reply = None
    else:
        session.step_session = new_step_session(step.agent)
        session.step_results = []
        session.clarify_rounds = 0
        query = build_step_query(step, session.outcomes)
    run = await delegate(session.agents, step, query, ctx, session.step_session)
    if run.result is not None:
        session.step_results.append(run.result)
    if run.question and session.clarify_rounds < MAX_CLARIFY_ROUNDS:
        session.clarify_rounds += 1
        session.step_question = run.question
        session.phase = "clarify"
        return None
    status = "blocked" if run.question else run.status
    session.outcomes.append(
        StepOutcome(
            step_id=step.id,
            agent=step.agent,
            status=status,
            attempt=session.retries.get(step.id, 0) + 1,
            results=list(session.step_results),
            question=run.question,
            error=run.error,
        )
    )
    _close_step_session(session)
    session.phase = "review"
    return None


async def _clarify(session: MainSession, ctx: ToolContext) -> MainResult | None:
    step = _plan(session).steps[session.step_index]
    messages: list[AnyMessage] = [
        SystemMessage(content=main_system_prompt(ctx.scenario_time, "clarify")),
        HumanMessage(
            content=_situation(
                session,
                current_step=step.model_dump(),
                step_outputs_so_far=[compact_json(result) for result in session.step_results],
                subagent_question=session.step_question,
            )
        ),
    ]
    end = await _tool_loop(session, ctx, messages, CLARIFY_TERMINALS, max_turns=DECISION_TURNS)
    if end.name == ANSWER_SUBAGENT:
        session.step_reply = end.args["answer"]
        session.phase = "execute"
        return None
    question = end.args.get("question") if end.name == ASK_HUMAN else None
    return _await_answer(session, question or session.step_question or "The sub-agent needs more context.")


async def _review(session: MainSession, ctx: ToolContext) -> MainResult | None:
    plan = _plan(session)
    step = plan.steps[session.step_index]
    extra: dict[str, Any] = {"finished_step": step.id}
    if session.review_feedback:
        extra["commander_rejected_revision"] = session.review_feedback
    messages: list[AnyMessage] = [
        SystemMessage(content=main_system_prompt(ctx.scenario_time, "review")),
        HumanMessage(content=_situation(session, **extra)),
    ]
    end = await _tool_loop(
        session,
        ctx,
        messages,
        REVIEW_TERMINALS,
        max_turns=DECISION_TURNS,
        check=lambda name, args: _review_problem(session, name, args),
    )
    session.review_feedback = ""
    outcome = session.outcomes[-1]
    if not end.name:
        session.abort_reason = "the review did not return a decision"
        session.phase = "respond"
        return None
    outcome.criteria_met = bool(end.args.get("criteria_met", False))
    outcome.review_note = str(end.args.get("note") or end.args.get("reason") or "")
    if end.name == CONTINUE_PLAN:
        _apply_edits(session, ContinuePlanInput.model_validate(end.args))
        session.step_index += 1
        session.phase = "execute"
        return None
    if end.name == REVISE_PLAN:
        return _apply_revision(session, RevisePlanInput.model_validate(end.args))
    if end.name == RETRY_STEP:
        retry = RetryStepInput.model_validate(end.args)
        session.retries[step.id] = session.retries.get(step.id, 0) + 1
        patched = step.model_copy(update={"context": f"{step.context}\nRetry: {retry.context_patch}".strip()})
        _replace_steps(session, {step.id: patched})
        session.phase = "execute"
        return None
    session.abort_reason = outcome.review_note
    session.phase = "respond"
    return None


async def _respond(session: MainSession, ctx: ToolContext) -> MainResult | None:
    extra: dict[str, Any] = {}
    if session.abort_reason:
        extra["aborted"] = session.abort_reason
    messages: list[AnyMessage] = [
        SystemMessage(content=main_system_prompt(ctx.scenario_time, "respond")),
        HumanMessage(content=_situation(session, **extra)),
    ]
    end = await _tool_loop(session, ctx, messages, RESPOND_TERMINALS, max_turns=DECISION_TURNS)
    if end.name != RESPOND:
        return _finish(session, "blocked", answer="The final answer could not be written; see the outcomes.")
    return _finish(session, "answered", answer=end.args["answer"])


# Tool loop shared by every phase.


async def _tool_loop(
    session: MainSession,
    ctx: ToolContext,
    messages: list[AnyMessage],
    terminals: dict[str, type[BaseModel]],
    *,
    query_budget: int = 0,
    max_turns: int,
    allow_ask: bool = False,
    check: Check | None = None,
) -> LoopEnd:
    """Run model turns until a valid terminal call, an ask, or the turn limit.

    Every tool call is answered in ``messages`` except a chosen ask_question, whose id is
    kept on the session so the commander's answer can be sent as its reply.
    """
    tools = terminal_tools(terminals)
    if query_budget > 0 or allow_ask:
        tools = [*query_tools(), *tools]
    bound = session.model.bind_tools(tools)
    budget = query_budget
    for _ in range(max_turns):
        response = await bound.ainvoke(messages)
        messages.append(response)
        calls = [_as_call(raw, index) for index, raw in enumerate(getattr(response, "tool_calls", None) or [])]
        if not calls:
            text = response.content.strip() if isinstance(response.content, str) else ""
            if RESPOND in terminals and text:
                return LoopEnd(RESPOND, {"answer": text})
            messages.append(HumanMessage(content=f"Call one of these tools: {', '.join(terminals)}."))
            continue
        end: LoopEnd | None = None
        ask: dict[str, Any] | None = None
        replies: list[ToolMessage] = []
        for call in calls:
            name = call["name"]
            if name in QUERY_TOOL_AGENTS and query_budget > 0:
                if budget <= 0:
                    replies.append(_reply(call, {"ok": False, "error": "tool budget spent"}))
                    continue
                budget -= 1
                record = await _run_query_agent(session, ctx, name, call["args"])
                replies.append(_reply(call, _record_content(record)))
                continue
            if name in terminals:
                problem = _args_problem(terminals[name], call["args"])
                if problem is None and check is not None:
                    problem = check(name, call["args"])
                if problem is None and end is not None:
                    problem = "only one control call per turn"
                if problem is not None:
                    replies.append(_reply(call, {"ok": False, "error": problem}))
                    continue
                end = LoopEnd(name, terminals[name].model_validate(call["args"]).model_dump(mode="json"))
                replies.append(_reply(call, {"ok": True}))
                continue
            if name == ASK_NAME and allow_ask and ask is None:
                ask = call
                continue
            replies.append(_reply(call, {"ok": False, "error": f"unknown tool: {name}"}))
        if ask is not None and end is not None:
            replies.append(_reply(ask, {"ok": False, "error": "not sent; a control call ended this turn"}))
        messages.extend(replies)
        if end is not None:
            return end
        if ask is not None:
            session.pending_ask_id = ask["id"]
            question = str(ask["args"].get("question") or "").strip()
            return LoopEnd(ASK_NAME, {"question": question or "Need more context before planning."})
    return LoopEnd("")


async def _run_query_agent(session: MainSession, ctx: ToolContext, name: str, args: dict[str, Any]) -> ToolCallRecord:
    agent = session.agents.get(QUERY_TOOL_AGENTS[name])
    if agent is None:
        record = ToolCallRecord(name=name, args=args, ok=False, error=f"no delegate for {name}")
    else:
        record = await _run_sub(name, args, ctx, session, agent)
    session.turn_calls.append(record)
    return record


def _record_content(record: ToolCallRecord) -> dict[str, Any] | str:
    if record.ok:
        return compact_json(record.data)
    return {"ok": False, "error": record.error}


def _reply(call: dict[str, Any], content: dict[str, Any] | str) -> ToolMessage:
    text = content if isinstance(content, str) else json.dumps(content)
    return ToolMessage(content=text, tool_call_id=call["id"], name=call["name"])


def _args_problem(schema: type[BaseModel], args: dict[str, Any]) -> str | None:
    try:
        schema.model_validate(args)
    except ValidationError as exc:
        return _validation_message(exc)
    return None


# Plan bookkeeping.


def _enter_mode(session: MainSession, ctx: ToolContext, mode: Literal["agent", "plan"]) -> None:
    phase: PromptPhase = "agent" if mode == "agent" else "discover"
    session.mode = mode
    session.phase = phase
    session.messages = [
        SystemMessage(content=main_system_prompt(ctx.scenario_time, phase)),
        HumanMessage(content=session.query),
    ]


def _plan(session: MainSession) -> WorkPlan:
    if session.plan is None:
        raise RuntimeError("no approved work plan")
    return session.plan


def _review_problem(session: MainSession, name: str, args: dict[str, Any]) -> str | None:
    plan = _plan(session)
    step = plan.steps[session.step_index]
    later = {s.id for s in plan.steps[session.step_index + 1 :]}
    if name == CONTINUE_PLAN:
        edits = ContinuePlanInput.model_validate(args).step_edits
        bad = [edit.step_id for edit in edits if edit.step_id not in later]
        if bad:
            return f"step_edits may only change steps that have not run; not {bad}"
        return None
    if name == REVISE_PLAN:
        remaining = RevisePlanInput.model_validate(args).remaining_steps
        return plan_problem(remaining, [s.id for s in plan.steps[: session.step_index + 1]])
    if name == RETRY_STEP and session.retries.get(step.id, 0) >= MAX_RETRIES:
        return f"step {step.id} was already retried; call continue_plan, revise_plan, or abort"
    return None


def _apply_edits(session: MainSession, decision: ContinuePlanInput) -> None:
    plan = _plan(session)
    by_id = {step.id: step for step in plan.steps}
    updates: dict[str, PlanStep] = {}
    for edit in decision.step_edits:
        current = updates.get(edit.step_id, by_id[edit.step_id])
        changes: dict[str, Any] = {}
        if edit.context is not None:
            changes["context"] = edit.context
        if edit.success_criteria:
            changes["success_criteria"] = edit.success_criteria
        updates[edit.step_id] = current.model_copy(update=changes)
    if updates:
        _replace_steps(session, updates)


def _replace_steps(session: MainSession, updates: dict[str, PlanStep]) -> None:
    plan = _plan(session)
    steps = [updates.get(step.id, step) for step in plan.steps]
    session.plan = plan.model_copy(update={"steps": steps})


def _apply_revision(session: MainSession, decision: RevisePlanInput) -> MainResult | None:
    plan = _plan(session)
    done = plan.steps[: session.step_index + 1]
    old_remaining = plan.steps[session.step_index + 1 :]
    revised = WorkPlan(goal=plan.goal, steps=[*done, *decision.remaining_steps], version=plan.version + 1)
    if is_material(old_remaining, decision.remaining_steps):
        session.proposed = revised
        session.approval_kind = "revision"
        return _await_approval(session)
    session.plan_history.append(plan)
    session.plan = revised
    session.step_index += 1
    session.phase = "execute"
    return None


def is_material(old: Sequence[PlanStep], new: Sequence[PlanStep]) -> bool:
    """A step added, removed, reordered, or handed to a different agent."""
    return [(step.id, step.agent) for step in old] != [(step.id, step.agent) for step in new]


def _deliver_answer(session: MainSession, answer: str) -> None:
    if session.phase == "clarify":
        session.step_reply = answer
        session.phase = "execute"
        return
    if session.pending_ask_id is None:
        session.messages.append(HumanMessage(content=answer))
        return
    session.messages.append(ToolMessage(content=answer, tool_call_id=session.pending_ask_id, name=ASK_NAME))
    session.pending_ask_id = None


def _apply_approval(session: MainSession, approval: PlanApproval | None) -> None:
    proposed = session.proposed
    if approval is None or proposed is None:
        raise ValueError("no work plan is waiting for approval")
    session.proposed = None
    if approval.approved:
        if session.plan is not None:
            session.plan_history.append(session.plan)
        session.plan = proposed
        session.step_index = 0 if session.approval_kind == "initial" else session.step_index + 1
        session.phase = "execute"
        return
    if session.approval_kind == "revision":
        session.review_feedback = approval.feedback or "rejected without feedback"
        session.phase = "review"
        return
    session.plan_history.append(proposed)
    feedback = approval.feedback or "no feedback given"
    session.messages.append(
        HumanMessage(content=f"The commander rejected work plan version {proposed.version}. Feedback: {feedback}")
    )
    session.phase = "discover"


def _close_step_session(session: MainSession) -> None:
    if session.step_session is not None:
        session.step_session.close()
    session.step_session = None
    session.step_question = None
    session.step_results = []


def _situation(session: MainSession, **extra: Any) -> str:
    """Request, plan with step states, and compact outcomes, as JSON for a decision phase."""
    plan = session.plan
    steps: list[dict[str, Any]] = []
    if plan is not None:
        for index, step in enumerate(plan.steps):
            state = "done" if index < session.step_index else "current" if index == session.step_index else "pending"
            steps.append({"state": state, **step.model_dump()})
    outcomes = [
        {
            "step_id": outcome.step_id,
            "agent": outcome.agent,
            "status": outcome.status,
            "attempt": outcome.attempt,
            "error": outcome.error,
            "question": outcome.question,
            "criteria_met": outcome.criteria_met,
            "output": compact_json(outcome.results[-1]) if outcome.results else None,
        }
        for outcome in session.outcomes
    ]
    payload = {
        "request": session.query,
        "goal": plan.goal if plan else None,
        "plan_version": plan.version if plan else None,
        "steps": steps,
        "outcomes": outcomes,
        **extra,
    }
    return json.dumps(payload, default=str)


# Results.


def _await_answer(session: MainSession, question: str) -> MainResult:
    session.awaiting = "answer"
    return _result(session, "awaiting_answer", question=question)


def _await_approval(session: MainSession) -> MainResult:
    session.awaiting = "approval"
    session.phase = "approve"
    return _result(session, "awaiting_approval", plan=session.proposed)


def _finish(session: MainSession, status: MainStatus, *, answer: str) -> MainResult:
    result = _result(session, status, answer=answer)
    session.close()
    return result


def _result(
    session: MainSession,
    status: MainStatus,
    *,
    question: str | None = None,
    answer: str = "",
    plan: WorkPlan | None = None,
) -> MainResult:
    waiting = status in ("awaiting_answer", "awaiting_approval")
    return MainResult(
        query=session.query,
        mode=session.mode,
        status=status,
        question=question,
        plan=plan or session.plan,
        outcomes=list(session.outcomes),
        answer=answer,
        calls=list(session.turn_calls),
        session=session if waiting else None,
    )
