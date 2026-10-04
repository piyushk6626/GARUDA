"""Retrieval sub-agent: a scripted model selects tools; the return path does not call it."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage

from garuda.contracts.tool import ToolContext
from garuda.harness.llm import DEFAULT_MODEL, chat_model
from garuda.harness.retrieval import RetrievalSession, retrieve
from garuda.harness.retrieval.prompts import retrieval_system_prompt
from garuda.harness.retrieval.tools import ASK_NAME, DATA_TOOL_NAMES, FINISH_NAME

WHEN = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)


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


def _ctx(doctrine: Any = None, embedder: Any = None) -> ToolContext:
    blank = SimpleNamespace()
    return ToolContext(
        scenario=blank,
        plans=blank,
        models=blank,
        providers=blank,
        scenario_time=WHEN,
        doctrine=doctrine,
        embedder=embedder,
    )


def _tool_call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _calls(*calls: tuple[str, dict, str]) -> AIMessage:
    return AIMessage(content="", tool_calls=[_tool_call(name, args, call_id) for name, args, call_id in calls])


def test_finish_returns_stub_data_without_another_model_call():
    model = ScriptedModel(
        [
            _calls(("get_aircraft_availability", {"base_ids": ["B1"]}, "c1")),
            _calls((FINISH_NAME, {}, "c2")),
            AIMessage(content="this summary must not be requested"),
        ]
    )

    result = asyncio.run(retrieve("Which aircraft are free at B1?", _ctx(), model=model))

    assert model.invocations == 2
    assert len(model.script) == 1
    assert {tool.name for tool in model.tools} == {*DATA_TOOL_NAMES, FINISH_NAME, ASK_NAME}
    assert result.query == "Which aircraft are free at B1?"
    assert len(result.calls) == 1
    call = result.calls[0]
    assert call.name == "get_aircraft_availability"
    assert call.args == {"base_ids": ["B1"]}
    assert call.ok is True
    assert call.error is None
    assert call.data["items"][0]["tail_no"] == "SU-A0123"
    assert "SU-A0123" in model.seen[1][-1].content


def test_plain_text_is_a_question_and_runs_no_tools():
    model = ScriptedModel([AIMessage(content="Which base should I check?")])
    session = RetrievalSession()

    result = asyncio.run(retrieve("hello", _ctx(), model=model, session=session))

    assert model.invocations == 1
    assert result.calls == []
    assert result.question == "Which base should I check?"
    assert result.session is session
    assert session.open is True
    assert session.messages


def test_parallel_tools_then_finish():
    model = ScriptedModel(
        [
            _calls(
                ("get_aircraft_availability", {}, "a"),
                ("get_crew_status", {}, "b"),
            ),
            _calls((FINISH_NAME, {}, "c")),
        ]
    )

    result = asyncio.run(retrieve("aircraft and crew", _ctx(), model=model))

    assert model.invocations == 2
    assert [call.name for call in result.calls] == ["get_aircraft_availability", "get_crew_status"]
    assert all(call.ok for call in result.calls)


def test_finish_in_the_same_turn_does_not_call_the_model_again():
    model = ScriptedModel(
        [
            _calls(
                ("get_weapon_stock", {}, "w"),
                (FINISH_NAME, {}, "f"),
            ),
            AIMessage(content="unused"),
        ]
    )

    result = asyncio.run(retrieve("weapon stock", _ctx(), model=model))

    assert model.invocations == 1
    assert [call.name for call in result.calls] == ["get_weapon_stock"]
    assert result.calls[0].ok is True
    assert result.calls[0].data["items"][0]["weapon_code"] == "AGM-1"


def test_invalid_args_are_returned_for_a_later_call():
    model = ScriptedModel(
        [
            _calls(("get_civil_traffic", {}, "bad")),
            _calls(
                (
                    "get_civil_traffic",
                    {"polygon": [[28.0, 77.0], [28.1, 77.1]], "since": "2026-06-01T06:00:00Z"},
                    "good",
                )
            ),
            _calls((FINISH_NAME, {}, "done")),
        ]
    )

    result = asyncio.run(retrieve("civil traffic", _ctx(), model=model))

    assert model.invocations == 3
    assert result.calls[0].ok is False
    assert result.calls[0].error
    assert result.calls[1].ok is True
    assert result.calls[1].data["items"][0]["icao24"] == "abc123"


def test_budget_stops_before_another_model_call():
    model = ScriptedModel(
        [
            _calls(
                ("get_aircraft_availability", {}, "a1"),
                ("get_crew_status", {}, "a2"),
            ),
            AIMessage(content="unused"),
        ]
    )

    result = asyncio.run(retrieve("both", _ctx(), tool_budget=1, model=model))

    assert model.invocations == 1
    assert [call.name for call in result.calls] == ["get_aircraft_availability", "get_crew_status"]
    assert result.calls[0].ok is True
    assert result.calls[1].ok is False
    assert result.calls[1].error == "tool budget spent"


def test_search_doctrine_returns_passages():
    model = ScriptedModel(
        [
            _calls(("search_doctrine", {"query": "tanker join procedure"}, "d1")),
            _calls((FINISH_NAME, {}, "d2")),
        ]
    )

    class _Reader:
        async def read(self, sql: str, args: tuple, *, timeout_s: float) -> list[dict]:
            return [{"text": "Join the tanker from astern.", "doc": "sop-1", "score": 0.9}]

    class _Embedder:
        async def embed(self, text: str) -> list[float]:
            return [0.0] * 3072

    result = asyncio.run(
        retrieve(
            "What does doctrine say about tanker join?",
            _ctx(doctrine=_Reader(), embedder=_Embedder()),
            model=model,
        )
    )

    assert model.invocations == 2
    assert result.calls[0].name == "search_doctrine"
    assert result.calls[0].ok is True
    assert result.calls[0].data["passages"][0]["doc"] == "sop-1"


def test_system_prompt_names_each_tool_and_the_limits():
    text = retrieval_system_prompt(WHEN)

    for name in DATA_TOOL_NAMES:
        assert name in text
    assert ASK_NAME in text
    assert FINISH_NAME in text
    assert "Do not create data" in text
    assert "parent agent or a human" in text
    assert "only for this retrieval session" in text


def test_default_model_is_gpt_6_luna(monkeypatch):
    monkeypatch.delenv("GARUDA_LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    model = chat_model()

    assert DEFAULT_MODEL == "gpt-6-luna"
    assert model.model_name == "gpt-6-luna"
    assert model.reasoning_effort == "none"


def test_question_stays_in_session_until_finish_then_is_dropped():
    model = ScriptedModel(
        [
            _calls((ASK_NAME, {"question": "Which base?"}, "q1")),
            _calls(("get_aircraft_availability", {"base_ids": ["B1"]}, "c1")),
            _calls((FINISH_NAME, {}, "c2")),
            _calls((FINISH_NAME, {}, "c3")),
        ]
    )
    session = RetrievalSession()

    asked = asyncio.run(retrieve("Which aircraft are free?", _ctx(), model=model, session=session))

    assert asked.question == "Which base?"
    assert asked.calls == []
    assert model.invocations == 1
    assert session.open is True

    done = asyncio.run(retrieve("B1", _ctx(), model=model, session=session))

    assert done.question is None
    assert done.session is None
    assert done.calls[0].name == "get_aircraft_availability"
    assert done.calls[0].data["items"][0]["tail_no"] == "SU-A0123"
    assert model.invocations == 3
    answer = model.seen[1]
    assert any(getattr(message, "content", "") == "B1" for message in answer)
    assert any(
        call.get("args", {}).get("question") == "Which base?"
        for message in answer
        for call in (getattr(message, "tool_calls", None) or [])
    )
    assert session.open is False
    assert session.messages == []

    asyncio.run(retrieve("crew at B1", _ctx(), model=model, session=session))

    later = " ".join(str(message.content) for message in model.seen[-1])
    assert "Which base?" not in later
    assert "SU-A0123" not in later
    assert "crew at B1" in later
