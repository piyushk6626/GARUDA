"""Spatial sub-agent: a scripted model picks geometry ops; spatial_query runs them."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from garuda.bases import BASES
from garuda.contracts.tool import ToolContext
from garuda.generators.gis import great_circle_km
from garuda.harness.retrieval import RetrievalSession
from garuda.harness.retrieval.tools import ASK_NAME, FINISH_NAME
from garuda.harness.spatial import query_geometry
from garuda.harness.spatial.prompts import spatial_system_prompt
from garuda.harness.spatial.tools import SPATIAL_TOOL_NAME
from garuda.schemas.gis import BaseRecord
from garuda.schemas.mission import Target

WHEN = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
OPS = ("distance_km", "within_radius", "nearest", "intersects", "buffer")
TARGET_LAT = 27.0
TARGET_LON = 74.0


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


def _base(airfield) -> BaseRecord:
    return BaseRecord(
        base_id=airfield.base_id,
        name=airfield.name,
        icao=airfield.icao,
        lat=airfield.lat,
        lon=airfield.lon,
        elevation_ft=airfield.elevation_ft,
        runway_count=1,
        runway_length_m=2000,
        shelter_count=8,
        fuel_capacity_l=1000,
        ground_crew_teams=1,
        max_sorties_per_hour=1.0,
    )


def _world() -> SimpleNamespace:
    return SimpleNamespace(
        bases=[_base(BASES[0]), _base(BASES[1])],
        features=[],
        borders=[],
        targets=[
            Target(
                target_id="T-7",
                target_class="RUNWAY",
                lat=TARGET_LAT,
                lon=TARGET_LON,
                value=1.0,
                hardness=0.5,
                defended_by=[],
            )
        ],
    )


def _ctx() -> ToolContext:
    blank = SimpleNamespace()
    scenario = SimpleNamespace(snapshot=lambda: _world())
    return ToolContext(scenario=scenario, plans=blank, models=blank, providers=blank, scenario_time=WHEN)


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


def test_distance_call_then_finish_returns_geometry():
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

    b1 = BASES[0]
    expected_km = round(great_circle_km(b1.lat, b1.lon, TARGET_LAT, TARGET_LON), 3)
    assert model.invocations == 2
    call = result.calls[0]
    assert call.name == SPATIAL_TOOL_NAME
    assert call.ok is True
    assert call.data["op"] == "distance_km"
    assert call.data["value_km"] == expected_km
    assert call.data["hits"] == []
    assert str(expected_km) in model.seen[1][-1].content


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
