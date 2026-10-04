"""Simulation agent: a scripted model runs the two simulation tools and may call ontology and retrieval."""

import asyncio

import pytest
from langchain_core.messages import AIMessage
from sim.sim_fixtures import T0
from sim.sim_fixtures import ctx as world_ctx

from garuda.harness.ontology.graph import query_graph
from garuda.harness.retrieval.graph import retrieve
from garuda.harness.retrieval.schemas import RetrievalResult, ToolCallRecord
from garuda.harness.retrieval.tools import ASK_NAME, FINISH_NAME
from garuda.harness.simulation import run_simulation
from garuda.harness.simulation.tools import default_sub_agents
from garuda.harness.tasks import TASKS, TaskSession
from garuda.harness.tasks.tools import ASK_ONTOLOGY, ASK_RETRIEVAL, ASK_SPATIAL

WHEN_ISO = T0.isoformat()
MONTE_CARLO = "evaluate_plan_monte_carlo"
RL_RETASK = "rl_retask"
FINISH = {"status": "done", "assumptions": ["default run count"], "note": "ready for the main agent"}
VALID = {
    MONTE_CARLO: {"plan_id": "P1", "stress": "heavy_threat", "n_runs": 500},
    RL_RETASK: {
        "plan_id": "P1",
        "event": {"type": "aircraft_nmc", "entity_id": "SU-1", "time": WHEN_ISO},
    },
}
OUTPUT_KEY = {MONTE_CARLO: "value", RL_RETASK: "new_plan_id"}


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


def _ctx():
    return world_ctx()


def _calls(*calls: tuple[str, dict, str]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"} for name, args, call_id in calls],
    )


def _agents(**overrides):
    async def _unused(query, ctx, session=None, **kwargs):
        raise AssertionError(query)

    agents = {ASK_RETRIEVAL: _unused, ASK_ONTOLOGY: _unused}
    agents.update(overrides)
    return agents


def test_tools_are_the_two_simulations_ontology_retrieval_and_controls():
    model = ScriptedModel([_calls((FINISH_NAME, {"status": "blocked", "note": "stop"}, "f"))])

    result = asyncio.run(run_simulation("simulate it", _ctx(), model=model, sub_agents=_agents()))

    names = {tool.name for tool in model.tools}
    assert names == {MONTE_CARLO, RL_RETASK, ASK_ONTOLOGY, ASK_RETRIEVAL, ASK_NAME, FINISH_NAME}
    assert ASK_SPATIAL not in names
    assert names.isdisjoint(TASKS)
    prompt = model.seen[0][0].content
    assert WHEN_ISO in prompt
    assert "Do not invent" in prompt
    assert "advisory" in prompt
    assert result.task == "simulation"
    assert result.status == "blocked"
    assert result.note == "stop"
    assert "session" not in result.model_dump()


@pytest.mark.parametrize("tool", [MONTE_CARLO, RL_RETASK])
def test_simulation_tool_then_finish_returns_output_and_the_finish_note(tool):
    model = ScriptedModel(
        [
            _calls((tool, VALID[tool], "m1")),
            _calls((FINISH_NAME, FINISH, "f1")),
            AIMessage(content="must not be requested"),
        ]
    )
    session = TaskSession()

    result = asyncio.run(run_simulation("run it", _ctx(), model=model, session=session, sub_agents=_agents()))

    assert model.invocations == 2
    assert result.question is None
    assert result.session is None
    assert session.open is False
    assert result.status == "done"
    assert result.assumptions == ["default run count"]
    assert result.agent_calls == []
    call = result.main_calls[0]
    assert call.name == tool
    assert call.args == VALID[tool]
    assert call.ok is True
    assert OUTPUT_KEY[tool] in call.data


def test_both_tools_in_one_turn_are_main_calls():
    model = ScriptedModel(
        [
            _calls((MONTE_CARLO, VALID[MONTE_CARLO], "m1"), (RL_RETASK, VALID[RL_RETASK], "m2")),
            _calls((FINISH_NAME, FINISH, "f1")),
        ]
    )

    result = asyncio.run(run_simulation("both", _ctx(), model=model, sub_agents=_agents()))

    assert [call.name for call in result.main_calls] == [MONTE_CARLO, RL_RETASK]
    assert all(call.ok for call in result.main_calls)


def test_retrieval_call_is_an_agent_call_and_reuses_its_session():
    seen: list = []

    async def fake_retrieval(query, ctx, session=None, **kwargs):
        seen.append(session)
        if len(seen) == 1:
            return RetrievalResult(query=query, question="Which aircraft?")
        return RetrievalResult(
            query=query,
            calls=[ToolCallRecord(name="get_aircraft_availability", args={}, ok=True, data={"items": []})],
        )

    model = ScriptedModel(
        [
            _calls((ASK_RETRIEVAL, {"query": "aircraft status"}, "r1")),
            _calls((ASK_RETRIEVAL, {"query": "SU-A0123"}, "r2")),
            _calls((FINISH_NAME, {"status": "partial", "assumptions": [], "note": "still gathering"}, "f1")),
        ]
    )

    result = asyncio.run(
        run_simulation("event", _ctx(), model=model, sub_agents=_agents(**{ASK_RETRIEVAL: fake_retrieval}))
    )

    assert seen[0] is seen[1]
    assert seen[0].open is False
    assert result.main_calls == []
    assert [call.name for call in result.agent_calls] == [ASK_RETRIEVAL, ASK_RETRIEVAL]
    assert result.agent_calls[0].data["question"] == "Which aircraft?"
    assert result.agent_calls[1].data["calls"][0]["name"] == "get_aircraft_availability"
    assert result.status == "partial"


def test_spatial_call_is_rejected_as_unknown():
    async def spatial(query, ctx, session=None, **kwargs):
        raise AssertionError(query)

    model = ScriptedModel(
        [
            _calls((ASK_SPATIAL, {"query": "distance"}, "s1")),
            _calls((FINISH_NAME, {"status": "blocked", "assumptions": [], "note": "wrong agent"}, "f1")),
        ]
    )

    result = asyncio.run(
        run_simulation("geometry", _ctx(), model=model, sub_agents=_agents(**{ASK_SPATIAL: spatial}))
    )

    assert result.agent_calls[0].ok is False
    assert result.agent_calls[0].error == f"unknown tool: {ASK_SPATIAL}"


def test_ask_keeps_the_session_until_finish_closes_children():
    seen: list = []

    async def fake_ontology(query, ctx, session=None, **kwargs):
        seen.append(session)
        return RetrievalResult(query=query)

    agents = _agents(**{ASK_ONTOLOGY: fake_ontology})
    model = ScriptedModel(
        [
            _calls(
                (ASK_ONTOLOGY, {"query": "sorties in the plan"}, "o1"),
                (ASK_NAME, {"question": "Which plan?"}, "q1"),
            ),
            _calls((FINISH_NAME, {"status": "blocked", "assumptions": [], "note": "no plan id"}, "f1")),
        ]
    )
    session = TaskSession()

    asked = asyncio.run(run_simulation("evaluate", _ctx(), model=model, session=session, sub_agents=agents))

    assert asked.question == "Which plan?"
    assert asked.session is session
    assert session.children[ASK_ONTOLOGY] is seen[0]
    assert seen[0].open is True

    done = asyncio.run(run_simulation("plan-9", _ctx(), model=model, session=session, sub_agents=agents))

    assert done.question is None
    assert done.status == "blocked"
    assert session.open is False
    assert seen[0].open is False
    assert session.children == {}


@pytest.mark.parametrize("tool", [MONTE_CARLO, RL_RETASK])
def test_invalid_args_are_returned_for_a_retry(tool):
    model = ScriptedModel(
        [
            _calls((tool, {}, "bad")),
            _calls((tool, VALID[tool], "good")),
            _calls((FINISH_NAME, FINISH, "f1")),
        ]
    )

    result = asyncio.run(run_simulation("retry", _ctx(), model=model, sub_agents=_agents()))

    assert model.invocations == 3
    assert result.main_calls[0].ok is False
    assert "Field required" in result.main_calls[0].error
    assert result.main_calls[0].error in model.seen[1][-1].content
    assert result.main_calls[1].ok is True
    assert result.status == "done"


def test_budget_skips_the_call_past_the_limit():
    model = ScriptedModel(
        [
            _calls((MONTE_CARLO, VALID[MONTE_CARLO], "m1"), (ASK_RETRIEVAL, {"query": "threats"}, "r1")),
            AIMessage(content="must not be requested"),
        ]
    )

    result = asyncio.run(run_simulation("both", _ctx(), tool_budget=1, model=model, sub_agents=_agents()))

    assert model.invocations == 1
    assert result.main_calls[0].ok is True
    assert result.agent_calls[0].ok is False
    assert result.agent_calls[0].error == "tool budget spent"


def test_default_sub_agents_are_ontology_and_retrieval():
    agents = default_sub_agents()

    assert set(agents) == {ASK_RETRIEVAL, ASK_ONTOLOGY}
    assert agents[ASK_RETRIEVAL] is retrieve
    assert agents[ASK_ONTOLOGY] is query_graph
