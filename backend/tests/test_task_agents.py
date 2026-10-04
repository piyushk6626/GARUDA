"""Task agents: a scripted model runs one optimizer tool and may call the three query agents."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pydantic import create_model

from garuda.contracts.tool import ToolContext
from garuda.harness.ontology.graph import query_graph
from garuda.harness.retrieval.graph import retrieve
from garuda.harness.retrieval.schemas import RetrievalResult, ToolCallRecord
from garuda.harness.retrieval.tools import ASK_NAME, FINISH_NAME
from garuda.harness.spatial.graph import query_geometry
from garuda.harness.tasks import TASKS, TaskSession, run_task
from garuda.harness.tasks.tools import (
    ASK_ONTOLOGY,
    ASK_RETRIEVAL,
    ASK_SPATIAL,
    default_sub_agents,
)

WHEN = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
WHEN_ISO = WHEN.isoformat()
# Every solver reads the scenario or plan store. A blank context returns that error to the model.
NEEDS_SCENARIO = frozenset(TASKS)
FINISH = {"status": "done", "assumptions": ["default horizon"], "note": "ready for the main agent"}
MARKERS = {
    "assign_missions": "retask_rolling_horizon",
    "schedule_crew": "NO_QUALIFIED",
    "assign_weapons_to_targets": "min_destroy_prob",
    "compute_sortie_capacity": "shadow prices",
    "plan_route": "does not reserve airspace",
    "schedule_tanker_aar": "spare fuel",
    "deconflict_airspace": "include_civil",
    "retask_rolling_horizon": "assign_missions",
    "pareto_tradeoffs": "nsga2",
}
OUTPUT_KEY = {
    "assign_missions": "plan_id",
    "schedule_crew": "fatigue_after",
    "assign_weapons_to_targets": "allocations",
    "compute_sortie_capacity": "bottlenecks",
    "plan_route": "waypoints",
    "schedule_tanker_aar": "tanker_routes",
    "deconflict_airspace": "total_shift_min",
    "retask_rolling_horizon": "new_plan_id",
    "pareto_tradeoffs": "nadir",
}
VALID = {
    "assign_missions": {"scenario_time": WHEN_ISO},
    "schedule_crew": {"plan_id": "plan-1"},
    "assign_weapons_to_targets": {"target_ids": ["T1"], "scenario_time": WHEN_ISO},
    "compute_sortie_capacity": {"start": WHEN_ISO},
    "plan_route": {
        "origin": {"lat": 28.0, "lon": 77.0},
        "destination": {"lat": 29.0, "lon": 78.0},
        "aircraft_type": "Su-30MKI",
        "depart_time": WHEN_ISO,
    },
    "schedule_tanker_aar": {"plan_id": "plan-1"},
    "deconflict_airspace": {"plan_id": "plan-1"},
    "retask_rolling_horizon": {
        "plan_id": "plan-1",
        "event": {"type": "aircraft_nmc", "entity_id": "SU-A0123", "time": WHEN_ISO},
    },
    "pareto_tradeoffs": {"base_plan_id": "plan-1"},
}


class ScriptedModel:
    def __init__(self, script: list[AIMessage]) -> None:
        self.script = list(script)
        self.invocations = 0
        self.seen: list[list] = []
        self.tools: list = []

    def bind_tools(self, tools, **kwargs):
        self.tools = list(tools)
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


def _agents(**overrides):
    async def _unused(query, ctx, session=None, **kwargs):
        raise AssertionError(query)

    agents = {ASK_RETRIEVAL: _unused, ASK_SPATIAL: _unused, ASK_ONTOLOGY: _unused}
    agents.update(overrides)
    return agents


@pytest.mark.parametrize("task", list(TASKS))
def test_tools_are_the_main_tool_the_query_agents_and_controls(task):
    model = ScriptedModel([_calls((FINISH_NAME, {"status": "blocked", "note": "stop"}, "f"))])

    result = asyncio.run(run_task(task, "do the task", _ctx(), model=model, sub_agents=_agents()))

    names = {tool.name for tool in model.tools}
    assert names == {task, ASK_RETRIEVAL, ASK_SPATIAL, ASK_ONTOLOGY, ASK_NAME, FINISH_NAME}
    prompt = model.seen[0][0].content
    assert WHEN_ISO in prompt
    assert task in prompt
    assert MARKERS[task] in prompt
    assert "Do not invent" in prompt
    assert ASK_RETRIEVAL in prompt
    assert result.status == "blocked"
    assert result.note == "stop"
    assert "session" not in result.model_dump()


@pytest.mark.parametrize("task", list(TASKS))
def test_main_tool_then_finish_returns_output_and_the_finish_note(task):
    model = ScriptedModel(
        [
            _calls((task, VALID[task], "m1")),
            _calls((FINISH_NAME, FINISH, "f1")),
            AIMessage(content="must not be requested"),
        ]
    )
    session = TaskSession()

    result = asyncio.run(run_task(task, "run it", _ctx(), model=model, session=session, sub_agents=_agents()))

    assert model.invocations == 2
    assert result.query == "run it"
    assert result.question is None
    assert result.session is None
    assert session.open is False
    assert result.status == "done"
    assert result.assumptions == ["default horizon"]
    assert result.note == "ready for the main agent"
    call = result.main_calls[0]
    assert call.name == task
    assert call.args == VALID[task]
    assert result.agent_calls == []
    assert task in NEEDS_SCENARIO
    assert call.ok is False
    assert call.error


@pytest.mark.parametrize("task", list(TASKS))
def test_main_tool_output_is_returned_as_call_data(task, monkeypatch):
    output_model = create_model(f"{task}_output", **{OUTPUT_KEY[task]: (str, "solved")})

    async def solved(payload, ctx):
        return output_model()

    monkeypatch.setitem(TASKS, task, replace(TASKS[task], fn=solved))
    model = ScriptedModel([_calls((task, VALID[task], "m1")), _calls((FINISH_NAME, FINISH, "f1"))])

    result = asyncio.run(run_task(task, "run it", _ctx(), model=model, sub_agents=_agents()))

    call = result.main_calls[0]
    assert call.ok is True
    assert call.error is None
    assert call.data == {OUTPUT_KEY[task]: "solved"}


@pytest.mark.parametrize("task", list(TASKS))
def test_retrieval_call_is_recorded_as_an_agent_call(task):
    async def fake_retrieval(query, ctx, session=None, **kwargs):
        return RetrievalResult(
            query=query,
            calls=[ToolCallRecord(name="get_mission_requests", args={}, ok=True, data={"items": []})],
        )

    model = ScriptedModel(
        [
            _calls((ASK_RETRIEVAL, {"query": "pending missions"}, "r1")),
            _calls((FINISH_NAME, {"status": "partial", "assumptions": [], "note": "still gathering"}, "f1")),
        ]
    )

    result = asyncio.run(
        run_task(task, "look first", _ctx(), model=model, sub_agents=_agents(**{ASK_RETRIEVAL: fake_retrieval}))
    )

    assert result.main_calls == []
    assert len(result.agent_calls) == 1
    call = result.agent_calls[0]
    assert call.name == ASK_RETRIEVAL
    assert call.ok is True
    assert call.data["query"] == "pending missions"
    assert call.data["calls"][0]["name"] == "get_mission_requests"
    assert result.status == "partial"


@pytest.mark.parametrize("task", list(TASKS))
def test_child_question_reuses_the_same_session(task):
    seen: list = []

    async def fake_retrieval(query, ctx, session=None, **kwargs):
        seen.append(session)
        if len(seen) == 1:
            return RetrievalResult(query=query, question="Which base?")
        return RetrievalResult(query=query, calls=[])

    model = ScriptedModel(
        [
            _calls((ASK_RETRIEVAL, {"query": "who is rested"}, "r1")),
            _calls((ASK_RETRIEVAL, {"query": "B1"}, "r2")),
            _calls((FINISH_NAME, {"status": "done", "assumptions": [], "note": "answered"}, "f1")),
        ]
    )

    result = asyncio.run(
        run_task(task, "crew", _ctx(), model=model, sub_agents=_agents(**{ASK_RETRIEVAL: fake_retrieval}))
    )

    assert seen[0] is seen[1]
    assert seen[0].open is False
    assert result.agent_calls[0].data["question"] == "Which base?"
    assert result.agent_calls[1].data["question"] is None
    assert result.status == "done"
    assert model.invocations == 3


@pytest.mark.parametrize("task", list(TASKS))
def test_ask_keeps_the_session_until_finish_closes_children(task):
    seen: list = []

    async def fake_retrieval(query, ctx, session=None, **kwargs):
        seen.append(session)
        return RetrievalResult(query=query)

    agents = _agents(**{ASK_RETRIEVAL: fake_retrieval})
    model = ScriptedModel(
        [
            _calls(
                (ASK_RETRIEVAL, {"query": "crew on the plan"}, "r1"),
                (ASK_NAME, {"question": "Which plan?"}, "q1"),
            ),
            _calls((FINISH_NAME, {"status": "blocked", "assumptions": [], "note": "no plan id"}, "f1")),
        ]
    )
    session = TaskSession()

    asked = asyncio.run(run_task(task, "fill the slots", _ctx(), model=model, session=session, sub_agents=agents))

    assert asked.question == "Which plan?"
    assert asked.session is session
    assert session.open is True
    child = seen[0]
    assert child is session.children[ASK_RETRIEVAL]
    assert child.open is True
    assert model.invocations == 1

    done = asyncio.run(run_task(task, "plan-9", _ctx(), model=model, session=session, sub_agents=agents))

    assert done.question is None
    assert done.status == "blocked"
    assert done.note == "no plan id"
    assert done.session is None
    assert session.open is False
    assert child.open is False
    assert session.children == {}
    assert any(getattr(message, "content", "") == "plan-9" for message in model.seen[1])


@pytest.mark.parametrize("task", list(TASKS))
def test_invalid_main_tool_args_are_returned_for_a_retry(task):
    model = ScriptedModel(
        [
            _calls((task, {}, "bad")),
            _calls((task, VALID[task], "good")),
            _calls((FINISH_NAME, FINISH, "f1")),
        ]
    )

    result = asyncio.run(run_task(task, "retry", _ctx(), model=model, sub_agents=_agents()))

    assert model.invocations == 3
    assert result.main_calls[0].ok is False
    assert "Field required" in result.main_calls[0].error
    assert result.main_calls[0].error in model.seen[1][-1].content
    assert result.main_calls[1].args == VALID[task]
    assert result.status == "done"


@pytest.mark.parametrize("task", list(TASKS))
def test_budget_skips_the_call_past_the_limit(task):
    model = ScriptedModel(
        [
            _calls(
                (task, VALID[task], "m1"),
                (ASK_RETRIEVAL, {"query": "bases"}, "r1"),
            ),
            AIMessage(content="must not be requested"),
        ]
    )

    result = asyncio.run(run_task(task, "both", _ctx(), tool_budget=1, model=model, sub_agents=_agents()))

    assert model.invocations == 1
    assert len(result.main_calls) == 1
    assert result.main_calls[0].name == task
    assert result.agent_calls[0].name == ASK_RETRIEVAL
    assert result.agent_calls[0].ok is False
    assert result.agent_calls[0].error == "tool budget spent"


def test_unknown_task_raises_before_the_model_runs():
    with pytest.raises(ValueError, match="unknown task"):
        asyncio.run(run_task("not_a_task", "hello", _ctx()))


def test_invalid_finish_is_returned_so_the_model_can_correct_it():
    model = ScriptedModel(
        [
            _calls((FINISH_NAME, {"status": "nope"}, "bad")),
            _calls((FINISH_NAME, {"status": "blocked", "note": "missing plan"}, "ok")),
        ]
    )

    result = asyncio.run(run_task("schedule_crew", "fill slots", _ctx(), model=model, sub_agents=_agents()))

    assert model.invocations == 2
    assert "status" in model.seen[1][-1].content
    assert result.status == "blocked"
    assert result.note == "missing plan"
    assert result.main_calls == []


def test_default_sub_agents_are_the_three_query_agents():
    agents = default_sub_agents()

    assert set(agents) == {ASK_RETRIEVAL, ASK_SPATIAL, ASK_ONTOLOGY}
    assert agents[ASK_RETRIEVAL] is retrieve
    assert agents[ASK_SPATIAL] is query_geometry
    assert agents[ASK_ONTOLOGY] is query_graph


def test_registry_lists_each_optimizer_once():
    assert set(TASKS) == set(MARKERS)
    assert all(definition.tool_budget == 8 for definition in TASKS.values())
    assert all(definition.spec.name == name for name, definition in TASKS.items())
