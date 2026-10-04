"""Streaming chat API over the main agent."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from garuda.api.main import create_app
from garuda.contracts.tool import ToolContext
from garuda.harness.tasks.schemas import TaskResult
from garuda.harness.retrieval.schemas import ToolCallRecord

WHEN = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
ONE_STEP = {
    "goal": "Build a plan",
    "steps": [
        {
            "id": "s1",
            "agent": "assign_missions",
            "objective": "Build a plan for the pending missions",
            "success_criteria": ["status done and plan_id present"],
        }
    ],
}


class ScriptedModel:
    def __init__(self, script: list[AIMessage]) -> None:
        self.script = list(script)

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        if not self.script:
            raise AssertionError("model called after the script ended")
        return self.script.pop(0)


def _ctx() -> ToolContext:
    blank = SimpleNamespace()
    return ToolContext(scenario=blank, plans=blank, models=blank, providers=blank, scenario_time=WHEN)


def _call(name: str, args: dict, call_id: str = "c") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _events(response) -> list[dict]:
    events = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line.removeprefix("data: ")))
    return events


def _app(model, agents=None):
    return create_app(scenario=SimpleNamespace(), ctx=_ctx(), model=model, sub_agents=agents)


def test_chat_streams_phase_and_final_events():
    app = _app(ScriptedModel([AIMessage(content="Nothing to look up.")]), agents={})

    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json={"query": "hello", "mode": "agent"}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            events = _events(response)

    assert [event["type"] for event in events] == ["session", "phase", "final", "done"]
    assert events[1]["phase"] == "agent"
    assert events[2]["answer"] == "Nothing to look up."
    assert events[3]["status"] == "answered"


def test_resume_after_approval_streams_the_step():
    async def assign(query, ctx, session=None, **kwargs):
        return TaskResult(
            task="assign_missions",
            query=query,
            main_calls=[ToolCallRecord(name="assign_missions", args={}, ok=True, data={"plan_id": "plan-7"})],
            status="done",
        )

    model = ScriptedModel(
        [
            _call("propose_plan", ONE_STEP, "p1"),
            _call("continue_plan", {"criteria_met": True}),
            _call("respond", {"answer": "plan-7 is ready."}),
        ]
    )
    app = _app(model, agents={"assign_missions": assign})

    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json={"query": "Build a plan", "mode": "plan"}) as response:
            proposed = _events(response)
        session_id = proposed[0]["session_id"]
        assert [event["type"] for event in proposed] == ["session", "phase", "plan_proposed", "done"]
        assert proposed[-1]["status"] == "awaiting_approval"

        with client.stream("POST", f"/api/chat/{session_id}/resume", json={"approved": True}) as response:
            done = _events(response)

        kinds = [event["type"] for event in done]
        assert kinds[0] == "session"
        assert kinds.index("step_start") < kinds.index("step_end") < kinds.index("final")
        assert done[-1] == {"type": "done", "status": "answered"}
        final = next(event for event in done if event["type"] == "final")
        assert final["answer"] == "plan-7 is ready."

        closed = client.post(f"/api/chat/{session_id}/resume", json={"approved": True})
        assert closed.status_code == 409


def test_unknown_session_is_not_found():
    app = _app(ScriptedModel([]), agents={})

    with TestClient(app) as client:
        response = client.post("/api/chat/missing/resume", json={"answer": "72 hours"})

    assert response.status_code == 404
