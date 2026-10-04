"""Ontology sub-agent: a scripted model picks templates; rows come from the graph reader."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage

from garuda.contracts.tool import ToolContext
from garuda.harness.ontology import query_graph
from garuda.harness.ontology.prompts import ontology_system_prompt
from garuda.harness.ontology.tools import TEMPLATE_NAMES
from garuda.harness.retrieval import RetrievalSession
from garuda.harness.retrieval.tools import ASK_NAME, FINISH_NAME

WHEN = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
ROWS = [{"tail_no": "SU-A0123", "aircraft_type": "Su-30MKI", "crew_id": "C-001"}]


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


class FakeGraphReader:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def read(self, cypher: str, params: dict[str, Any], *, timeout_s: float) -> list[dict[str, Any]]:
        self.calls.append(params)
        return list(ROWS)


def _ctx(reader: FakeGraphReader) -> ToolContext:
    blank = SimpleNamespace()
    return ToolContext(scenario=blank, plans=blank, models=blank, providers=blank, scenario_time=WHEN, graph=reader)


def _calls(*calls: tuple[str, dict, str]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"} for name, args, call_id in calls],
    )


def test_tools_are_templates_and_controls_only():
    reader = FakeGraphReader()
    model = ScriptedModel([_calls((FINISH_NAME, {}, "f"))])

    asyncio.run(query_graph("anything", _ctx(reader), model=model))

    names = {tool.name for tool in model.tools}
    assert names == {*TEMPLATE_NAMES, FINISH_NAME, ASK_NAME}
    assert "query_ontology" not in names
    for tool in model.tools:
        assert "cypher" not in tool.args


def test_template_call_then_finish_returns_rows():
    reader = FakeGraphReader()
    model = ScriptedModel(
        [
            _calls(("aircraft_with_qualified_crew", {"base_id": "B1", "max_fatigue": 0.6}, "c1")),
            _calls((FINISH_NAME, {}, "c2")),
            AIMessage(content="must not be requested"),
        ]
    )

    result = asyncio.run(query_graph("Which jets at B1 have rested crew?", _ctx(reader), model=model))

    assert model.invocations == 2
    call = result.calls[0]
    assert call.name == "aircraft_with_qualified_crew"
    assert call.ok is True
    assert call.data["rows"] == ROWS
    assert reader.calls[0]["base_id"] == "B1"
    assert reader.calls[0]["max_fatigue"] == 0.6
    assert "SU-A0123" in model.seen[1][-1].content


def test_bad_args_are_errors_and_do_not_query():
    reader = FakeGraphReader()
    model = ScriptedModel(
        [
            _calls(
                ("assets_at_base", {"base_id": "B1", "cypher": "MATCH (n) RETURN n"}, "a"),
                ("threats_covering_target", {}, "b"),
                ("query_ontology", {"template": "assets_at_base", "params": {}}, "c"),
            ),
            _calls((FINISH_NAME, {}, "d")),
        ]
    )

    result = asyncio.run(query_graph("bad", _ctx(reader), model=model))

    assert [call.ok for call in result.calls] == [False, False, False]
    assert "cypher" in result.calls[0].error
    assert "target_id" in result.calls[1].error
    assert result.calls[2].error == "unknown template: query_ontology"
    assert reader.calls == []


def test_question_resumes_the_same_session():
    reader = FakeGraphReader()
    model = ScriptedModel(
        [
            _calls((ASK_NAME, {"question": "Which target?"}, "q1")),
            _calls(("threats_covering_target", {"target_id": "T-7"}, "c1")),
            _calls((FINISH_NAME, {}, "c2")),
        ]
    )
    session = RetrievalSession()

    asked = asyncio.run(query_graph("What defends the target?", _ctx(reader), model=model, session=session))

    assert asked.question == "Which target?"
    assert session.open is True

    done = asyncio.run(query_graph("T-7", _ctx(reader), model=model, session=session))

    assert done.question is None
    assert done.calls[0].name == "threats_covering_target"
    assert reader.calls[0]["target_id"] == "T-7"
    assert session.open is False


def test_system_prompt_names_each_template_and_forbids_cypher():
    text = ontology_system_prompt(WHEN)

    for name in TEMPLATE_NAMES:
        assert name in text
    assert "cannot write, show, or describe Cypher" in text
    assert "Do not invent" in text
