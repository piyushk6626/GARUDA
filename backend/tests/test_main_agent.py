"""Main agent: a scripted model routes, plans, delegates to fake sub-agents, reviews, and responds."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from garuda.contracts.tool import ToolContext
from garuda.harness.main import PlanApproval, resume_main, run_main
from garuda.harness.main.agents import compact_json, default_agents
from garuda.harness.main.graph import is_material
from garuda.harness.main.prompts import main_system_prompt
from garuda.harness.main.schemas import AGENT_NAMES, PlanStep
from garuda.harness.retrieval.schemas import RetrievalResult, ToolCallRecord
from garuda.harness.retrieval.tools import ASK_NAME
from garuda.harness.tasks import TASKS
from garuda.harness.tasks.schemas import TaskResult
from garuda.harness.tasks.session import TaskSession
from garuda.harness.tasks.tools import ASK_ONTOLOGY, ASK_RETRIEVAL, ASK_SPATIAL
from garuda.tools.shaping import shape_for_llm

WHEN = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)

S1 = {
    "id": "s1",
    "agent": "assign_missions",
    "objective": "Build a plan for the pending missions",
    "success_criteria": ["status done and plan_id present"],
}
S2 = {
    "id": "s2",
    "agent": "schedule_crew",
    "objective": "Crew the plan",
    "context": "plan_id from step s1",
    "inputs_from": ["s1"],
    "success_criteria": ["no unfilled slots"],
}
PLAN = {"goal": "Build and crew a plan", "steps": [S1, S2]}
ONE_STEP = {"goal": "Build a plan", "steps": [S1]}


class ScriptedModel:
    def __init__(self, script: list[AIMessage]) -> None:
        self.script = list(script)
        self.invocations = 0
        self.seen: list[list] = []
        self.tools: list[list] = []

    def bind_tools(self, tools, **kwargs):
        self.tools.append([tool.name for tool in tools])
        return self

    async def ainvoke(self, messages):
        self.invocations += 1
        self.seen.append(list(messages))
        if not self.script:
            raise AssertionError("model called after the script ended")
        return self.script.pop(0)


def _ctx() -> ToolContext:
    blank = SimpleNamespace()
    return ToolContext(scenario=blank, plans=blank, models=blank, providers=blank, scenario_time=WHEN)


def _calls(*calls: tuple[str, dict, str]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"} for name, args, call_id in calls],
    )


def _call(name: str, args: dict, call_id: str = "c") -> AIMessage:
    return _calls((name, args, call_id))


def _agents(**overrides):
    async def _unused(query, ctx, session=None, **kwargs):
        raise AssertionError(f"unexpected delegation: {query}")

    agents = {name: _unused for name in AGENT_NAMES}
    agents.update(overrides)
    return agents


def _task(task: str, data: dict, status: str = "done", question: str | None = None) -> TaskResult:
    return TaskResult(
        task=task,
        query="q",
        main_calls=[ToolCallRecord(name=task, args={}, ok=True, data=data)],
        status=None if question else status,
        question=question,
    )


def _texts(messages) -> str:
    return "\n".join(str(message.content) for message in messages)


def test_router_picks_agent_mode_and_answers_from_retrieval():
    async def retrieval(query, ctx, session=None, **kwargs):
        return RetrievalResult(
            query=query,
            calls=[ToolCallRecord(name="get_aircraft_availability", args={}, ok=True, data={"items": ["SU-A0123"]})],
        )

    model = ScriptedModel(
        [
            _call("choose_mode", {"mode": "agent", "reason": "lookup"}),
            _call(ASK_RETRIEVAL, {"query": "FMC Su-30s at Ambala"}, "r1"),
            _call("respond", {"answer": "SU-A0123 is FMC (get_aircraft_availability)."}),
        ]
    )

    result = asyncio.run(run_main("Which Su-30s are FMC?", _ctx(), model=model, sub_agents=_agents(retrieval=retrieval)))

    assert result.status == "answered"
    assert result.mode == "agent"
    assert result.answer.startswith("SU-A0123")
    assert result.session is None
    assert [call.name for call in result.calls] == [ASK_RETRIEVAL]
    assert result.calls[0].data["calls"][0]["data"]["items"] == ["SU-A0123"]
    assert model.tools[0] == ["choose_mode"]
    assert set(model.tools[1]) == {ASK_RETRIEVAL, ASK_SPATIAL, ASK_ONTOLOGY, ASK_NAME, "respond", "switch_to_plan"}
    assert "SU-A0123" in model.seen[2][-1].content


def test_forced_mode_skips_the_router():
    model = ScriptedModel([AIMessage(content="Nothing to look up.")])

    result = asyncio.run(run_main("hello", _ctx(), mode="agent", model=model, sub_agents=_agents()))

    assert model.invocations == 1
    assert "choose_mode" not in model.tools[0]
    assert result.status == "answered"
    assert result.answer == "Nothing to look up."


def test_agent_mode_switches_to_plan_mode():
    model = ScriptedModel(
        [
            _call("switch_to_plan", {"reason": "needs an optimizer"}, "sw"),
            _call("propose_plan", ONE_STEP, "p1"),
        ]
    )

    result = asyncio.run(run_main("Build a plan", _ctx(), mode="agent", model=model, sub_agents=_agents()))

    assert result.status == "awaiting_approval"
    assert result.mode == "plan"
    assert result.plan.version == 1
    assert "Current phase: discovery and planning" in model.seen[1][0].content
    assert "needs an optimizer" in _texts(model.seen[1])


def test_discovery_asks_the_commander_then_proposes_a_plan():
    model = ScriptedModel(
        [
            _call(ASK_NAME, {"question": "What horizon?"}, "q1"),
            _call("propose_plan", ONE_STEP, "p1"),
        ]
    )

    asked = asyncio.run(run_main("Plan tomorrow", _ctx(), mode="plan", model=model, sub_agents=_agents()))

    assert asked.status == "awaiting_answer"
    assert asked.question == "What horizon?"
    assert asked.session is not None

    proposed = asyncio.run(resume_main(asked.session, _ctx(), answer="72 hours"))

    assert proposed.status == "awaiting_approval"
    assert [step.id for step in proposed.plan.steps] == ["s1"]
    reply = model.seen[1][-1]
    assert isinstance(reply, ToolMessage)
    assert reply.tool_call_id == "q1"
    assert reply.content == "72 hours"


@pytest.mark.parametrize(
    ("bad_step", "error"),
    [
        ({**S1, "agent": "commit_plan"}, "unknown agent"),
        ({**S1, "inputs_from": ["s9"]}, "not earlier steps"),
    ],
)
def test_invalid_plan_is_returned_to_the_model(bad_step, error):
    model = ScriptedModel(
        [
            _call("propose_plan", {"goal": "g", "steps": [bad_step]}, "bad"),
            _call("propose_plan", ONE_STEP, "good"),
        ]
    )

    result = asyncio.run(run_main("Plan", _ctx(), mode="plan", model=model, sub_agents=_agents()))

    assert model.invocations == 2
    assert error in model.seen[1][-1].content
    assert result.status == "awaiting_approval"


def test_full_plan_with_rejection_review_edits_and_a_subagent_question():
    queries: dict[str, list] = {"assign_missions": [], "schedule_crew": []}

    async def assign(query, ctx, session=None, **kwargs):
        queries["assign_missions"].append((query, session))
        return _task("assign_missions", {"plan_id": "plan-7", "unassigned": []})

    async def crew(query, ctx, session=None, **kwargs):
        queries["schedule_crew"].append((query, session))
        if len(queries["schedule_crew"]) == 1:
            return _task("schedule_crew", {}, question="Which base?")
        return _task("schedule_crew", {"unfilled": []})

    model = ScriptedModel(
        [
            _call("choose_mode", {"mode": "plan"}),
            _call("propose_plan", PLAN, "p1"),
            _call("propose_plan", PLAN, "p2"),
            _call(
                "continue_plan",
                {"criteria_met": True, "step_edits": [{"step_id": "s2", "context": "plan_id plan-7"}]},
            ),
            _call("answer_subagent", {"answer": "B1"}),
            _call("continue_plan", {"criteria_met": True}),
            _call("respond", {"answer": "plan-7 is built and crewed (s1, s2)."}),
        ]
    )
    agents = _agents(assign_missions=assign, schedule_crew=crew)

    first = asyncio.run(run_main("Build and crew a plan", _ctx(), model=model, sub_agents=agents))
    assert first.status == "awaiting_approval"
    assert first.plan.version == 1

    second = asyncio.run(resume_main(first.session, _ctx(), approval=PlanApproval(approved=False, feedback="crew it too")))
    assert second.status == "awaiting_approval"
    assert second.plan.version == 2
    assert "crew it too" in _texts(model.seen[2])

    session = second.session
    done = asyncio.run(resume_main(session, _ctx(), approval=PlanApproval(approved=True)))

    assert done.status == "answered"
    assert done.answer.startswith("plan-7")
    assert [(o.step_id, o.status, o.criteria_met) for o in done.outcomes] == [("s1", "done", True), ("s2", "done", True)]
    crew_query, crew_session = queries["schedule_crew"][0]
    assert "plan_id plan-7" in crew_query
    assert "[s1]" in crew_query and "plan-7" in crew_query
    assert isinstance(crew_session, TaskSession)
    assert queries["schedule_crew"][1] == ("B1", crew_session)
    assert "Which base?" in model.seen[4][-1].content
    assert len(done.outcomes[1].results) == 2
    assert session.open is False
    assert session.step_session is None


def test_subagent_question_escalates_to_the_commander():
    seen: list = []

    async def crew(query, ctx, session=None, **kwargs):
        seen.append((query, session))
        if len(seen) == 1:
            return _task("schedule_crew", {}, question="Which plan?")
        return _task("schedule_crew", {"unfilled": []})

    plan = {"goal": "Crew", "steps": [{**S2, "inputs_from": [], "context": ""}]}
    model = ScriptedModel(
        [
            _call("propose_plan", plan, "p1"),
            _call("ask_human", {"question": "Which plan should be crewed?"}),
            _call("continue_plan", {"criteria_met": True}),
            _call("respond", {"answer": "Crewed."}),
        ]
    )

    proposed = asyncio.run(run_main("Crew it", _ctx(), mode="plan", model=model, sub_agents=_agents(schedule_crew=crew)))
    asked = asyncio.run(resume_main(proposed.session, _ctx(), approval=PlanApproval(approved=True)))

    assert asked.status == "awaiting_answer"
    assert asked.question == "Which plan should be crewed?"

    done = asyncio.run(resume_main(asked.session, _ctx(), answer="plan-3"))

    assert done.status == "answered"
    assert seen[1] == ("plan-3", seen[0][1])
    assert done.outcomes[0].status == "done"


def test_review_that_adds_a_step_needs_approval_again():
    async def assign(query, ctx, session=None, **kwargs):
        return _task("assign_missions", {"plan_id": "plan-7"})

    crew_queries: list[str] = []

    async def crew(query, ctx, session=None, **kwargs):
        crew_queries.append(query)
        return _task("schedule_crew", {"unfilled": []})

    added = {**S2, "context": "plan_id plan-7"}
    model = ScriptedModel(
        [
            _call("propose_plan", ONE_STEP, "p1"),
            _call("revise_plan", {"criteria_met": True, "reason": "needs crew", "remaining_steps": [added]}),
            _call("revise_plan", {"criteria_met": True, "reason": "still needs crew", "remaining_steps": [added]}),
            _call("continue_plan", {"criteria_met": True}),
            _call("respond", {"answer": "Done."}),
        ]
    )
    agents = _agents(assign_missions=assign, schedule_crew=crew)

    proposed = asyncio.run(run_main("Build", _ctx(), mode="plan", model=model, sub_agents=agents))
    revision = asyncio.run(resume_main(proposed.session, _ctx(), approval=PlanApproval(approved=True)))

    assert revision.status == "awaiting_approval"
    assert revision.plan.version == 2
    assert [step.id for step in revision.plan.steps] == ["s1", "s2"]
    assert crew_queries == []

    again = asyncio.run(
        resume_main(revision.session, _ctx(), approval=PlanApproval(approved=False, feedback="explain first"))
    )
    assert again.status == "awaiting_approval"
    assert "explain first" in model.seen[2][-1].content

    done = asyncio.run(resume_main(again.session, _ctx(), approval=PlanApproval(approved=True)))

    assert done.status == "answered"
    assert len(crew_queries) == 1
    assert "plan_id plan-7" in crew_queries[0]
    assert [o.step_id for o in done.outcomes] == ["s1", "s2"]


def test_minor_revision_continues_without_approval():
    async def assign(query, ctx, session=None, **kwargs):
        return _task("assign_missions", {"plan_id": "plan-7"})

    async def crew(query, ctx, session=None, **kwargs):
        return _task("schedule_crew", {"unfilled": []})

    reworded = {**S2, "objective": "Crew plan-7 at every base"}
    model = ScriptedModel(
        [
            _call("propose_plan", PLAN, "p1"),
            _call("revise_plan", {"criteria_met": True, "reason": "reword", "remaining_steps": [reworded]}),
            _call("continue_plan", {"criteria_met": True}),
            _call("respond", {"answer": "Done."}),
        ]
    )

    proposed = asyncio.run(
        run_main("Build", _ctx(), mode="plan", model=model, sub_agents=_agents(assign_missions=assign, schedule_crew=crew))
    )
    done = asyncio.run(resume_main(proposed.session, _ctx(), approval=PlanApproval(approved=True)))

    assert done.status == "answered"
    assert done.plan.version == 2
    assert done.plan.steps[1].objective == "Crew plan-7 at every base"


def test_retry_is_capped_at_one():
    calls: list[str] = []

    async def retrieval(query, ctx, session=None, **kwargs):
        calls.append(query)
        return RetrievalResult(
            query=query, calls=[ToolCallRecord(name="get_weapon_stock", args={}, ok=False, error="bad base")]
        )

    plan = {
        "goal": "Stock",
        "steps": [{"id": "s1", "agent": "retrieval", "objective": "Weapon stock", "success_criteria": ["stock listed"]}],
    }
    model = ScriptedModel(
        [
            _call("propose_plan", plan, "p1"),
            _call("retry_step", {"reason": "bad base", "context_patch": "base B1"}),
            _call("retry_step", {"reason": "again", "context_patch": "base B2"}, "r2"),
            _call("abort", {"reason": "stock unavailable"}, "a1"),
            _call("respond", {"answer": "Stock could not be read (s1)."}),
        ]
    )

    proposed = asyncio.run(run_main("Stock", _ctx(), mode="plan", model=model, sub_agents=_agents(retrieval=retrieval)))
    done = asyncio.run(resume_main(proposed.session, _ctx(), approval=PlanApproval(approved=True)))

    assert done.status == "answered"
    assert len(calls) == 2
    assert "Retry: base B1" in calls[1]
    assert [(o.attempt, o.status) for o in done.outcomes] == [(1, "failed"), (2, "failed")]
    assert "already retried" in model.seen[3][-1].content
    assert "stock unavailable" in model.seen[4][-1].content


def test_resume_rejects_the_wrong_input():
    model = ScriptedModel([_call("propose_plan", ONE_STEP, "p1")])
    proposed = asyncio.run(run_main("Build", _ctx(), mode="plan", model=model, sub_agents=_agents()))

    with pytest.raises(ValueError, match="plan approval"):
        asyncio.run(resume_main(proposed.session, _ctx(), answer="yes"))


def test_master_prompt_covers_every_agent_and_tool():
    prompt = main_system_prompt(WHEN, "review")

    assert WHEN.isoformat() in prompt
    for name in AGENT_NAMES:
        assert name in prompt
    for name in ("propose_plan", "continue_plan", "revise_plan", "retry_step", "abort", "ask_question", "ask_human"):
        assert name in prompt
    assert "never commit" in prompt.lower()


def test_default_agents_cover_the_registry():
    assert set(default_agents()) == set(AGENT_NAMES)
    assert set(TASKS) <= set(AGENT_NAMES)


def test_material_change_is_add_remove_reorder_or_reassign():
    a = PlanStep.model_validate(S1)
    b = PlanStep.model_validate({**S2, "inputs_from": []})

    assert not is_material([a, b], [a, b.model_copy(update={"context": "plan-7"})])
    assert is_material([a], [a, b])
    assert is_material([a, b], [b, a])
    assert is_material([a], [a.model_copy(update={"agent": "retask_rolling_horizon"})])


def test_shape_for_llm_cuts_long_lists():
    shaped = shape_for_llm({"items": list(range(30)), "nested": {"rows": list(range(5))}}, max_rows=10)

    assert shaped["items"] == list(range(10))
    assert shaped["items_total"] == 30
    assert shaped["items_truncated"] is True
    assert shaped["nested"] == {"rows": list(range(5))}
    assert len(compact_json({"items": ["x" * 100] * 500}, max_chars=2000)) <= 2000 + len("...[truncated]")


def test_rejected_initial_plan_feedback_is_a_human_message():
    model = ScriptedModel([_call("propose_plan", ONE_STEP, "p1"), _call("propose_plan", ONE_STEP, "p2")])
    proposed = asyncio.run(run_main("Build", _ctx(), mode="plan", model=model, sub_agents=_agents()))

    again = asyncio.run(resume_main(proposed.session, _ctx(), approval=PlanApproval(approved=False, feedback="fewer")))

    assert again.plan.version == 2
    assert isinstance(model.seen[1][-1], HumanMessage)
    assert "fewer" in model.seen[1][-1].content


def test_on_event_reports_phases_tools_and_the_answer():
    events = []

    async def on_event(event):
        events.append(event)

    async def retrieval(query, ctx, session=None, **kwargs):
        return RetrievalResult(
            query=query,
            calls=[ToolCallRecord(name="get_aircraft_availability", args={}, ok=True, data={"items": ["SU-A0123"]})],
        )

    model = ScriptedModel(
        [
            _call(ASK_RETRIEVAL, {"query": "FMC Su-30s"}, "r1"),
            _call("respond", {"answer": "SU-A0123 is FMC."}),
        ]
    )

    result = asyncio.run(
        run_main("Which Su-30s are FMC?", _ctx(), mode="agent", model=model, sub_agents=_agents(retrieval=retrieval), on_event=on_event)
    )

    assert result.status == "answered"
    assert [event["type"] for event in events] == ["phase", "tool_start", "tool_end", "final"]
    assert events[0]["phase"] == "agent"
    assert events[1]["name"] == ASK_RETRIEVAL
    assert events[2]["ok"] is True
    assert events[3]["answer"] == "SU-A0123 is FMC."


def test_on_event_reports_a_plan_and_each_step():
    events = []

    async def on_event(event):
        events.append(event["type"])

    async def assign(query, ctx, session=None, **kwargs):
        return _task("assign_missions", {"plan_id": "plan-7"})

    model = ScriptedModel(
        [
            _call("propose_plan", ONE_STEP, "p1"),
            _call("continue_plan", {"criteria_met": True}),
            _call("respond", {"answer": "Done."}),
        ]
    )
    agents = _agents(assign_missions=assign)

    proposed = asyncio.run(run_main("Build", _ctx(), mode="plan", model=model, sub_agents=agents, on_event=on_event))

    assert proposed.status == "awaiting_approval"
    assert events == ["phase", "plan_proposed"]

    done = asyncio.run(resume_main(proposed.session, _ctx(), approval=PlanApproval(approved=True), on_event=on_event))

    assert done.status == "answered"
    assert "step_start" in events
    assert "step_end" in events
    assert events[-1] == "final"
