"""Spatial sub-agent: a scripted model picks geometry ops; the stub tool returns them."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from garuda.contracts.tool import ToolContext
from garuda.harness.retrieval import RetrievalSession
from garuda.harness.retrieval.tools import ASK_NAME, FINISH_NAME
from garuda.harness.spatial import query_geometry
from garuda.harness.spatial.prompts import spatial_system_prompt
from garuda.harness.spatial.tools import SPATIAL_TOOL_NAME

WHEN = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
OPS = ("distance_km", "within_radius", "nearest", "intersects", "buffer")


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


def test_tools_are_spatial_query_and_controls_only():
    model = ScriptedModel([_calls((FINISH_NAME, {}, "f"))])

    asyncio.run(query_geometry("anything", _ctx(), model=model))

    names = {tool.name for tool in model.tools}
    assert names == {SPATIAL_TOOL_NAME, FINISH_NAME, ASK_NAME}


def test_distance_call_then_finish_returns_stub():
    model = ScriptedModel(
        [
            _calls(
                (
                    SPATIAL_TOOL_NAME,
                    {"op": "distance_km", "a": {"entity_id": "B1"}, "b": {"entity_id": "T-7"}},
                    "c1",
                )
            ),
            _calls((FINISH_NAME, {}, "c2")),
            AIMessage(content="must not be requested"),
        ]
    )

    result = asyncio.run(query_geometry("How far is B1 from T-7?", _ctx(), model=model))

    assert model.invocations == 2
    call = result.calls[0]
    assert call.name == SPATIAL_TOOL_NAME
    assert call.ok is True
    assert call.data["value_km"] == 120.0
    assert call.data["hits"] == ["B1"]
    assert "B1" in model.seen[1][-1].content


def test_bad_args_are_errors():
    model = ScriptedModel(
        [
            _calls(
                (SPATIAL_TOOL_NAME, {"op": "threat_risk", "a": {"entity_id": "B1"}}, "a"),
                (SPATIAL_TOOL_NAME, {"op": "nearest"}, "b"),
            ),
            _calls((FINISH_NAME, {}, "c")),
        ]
    )

    result = asyncio.run(query_geometry("bad", _ctx(), model=model))

    assert [call.ok for call in result.calls] == [False, False]
    assert "op" in result.calls[0].error
    assert "a" in result.calls[1].error


def test_question_resumes_the_same_session():
    model = ScriptedModel(
        [
            _calls((ASK_NAME, {"question": "Which two places?"}, "q1")),
            _calls(
                (
                    SPATIAL_TOOL_NAME,
                    {"op": "distance_km", "a": {"entity_id": "B1"}, "b": {"entity_id": "B2"}},
                    "c1",
                )
            ),
            _calls((FINISH_NAME, {}, "c2")),
        ]
    )
    session = RetrievalSession()

    asked = asyncio.run(query_geometry("How far apart are they?", _ctx(), model=model, session=session))

    assert asked.question == "Which two places?"
    assert session.open is True

    done = asyncio.run(query_geometry("B1 and B2", _ctx(), model=model, session=session))

    assert done.question is None
    assert done.calls[0].name == SPATIAL_TOOL_NAME
    assert done.calls[0].args["a"]["entity_id"] == "B1"
    assert session.open is False


def test_system_prompt_names_each_op_and_forbids_threat_maps():
    text = spatial_system_prompt(WHEN)

    for op in OPS:
        assert op in text
    assert "does not compute threat-risk maps" in text
    assert "Do not invent" in text
