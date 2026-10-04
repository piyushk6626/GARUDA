"""Retrieval sub-agent: the model selects tools, and the return path only packs their outputs."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from garuda.contracts.tool import ToolContext
from garuda.harness.retrieval.prompts import retrieval_system_prompt
from garuda.harness.retrieval.schemas import RetrievalResult, ToolCallRecord
from garuda.harness.retrieval.session import RetrievalSession
from garuda.harness.retrieval.state import RetrievalState
from garuda.harness.retrieval.tools import ASK_NAME, CONTROL_TOOL_NAMES, FINISH_NAME, model_tools, run_data_call

RunCall = Callable[[str, dict[str, Any], ToolContext], Awaitable[ToolCallRecord]]


def build_retrieval_graph(
    ctx: ToolContext,
    model: Any,
    *,
    tools: list[Any] | None = None,
    run_call: RunCall = run_data_call,
):
    """Compile a graph closed over ``ctx``. ToolContext is not checkpoint state."""
    if tools is None:
        tools = model_tools()

    async def select(state: RetrievalState) -> dict[str, Any]:
        response = await model.bind_tools(tools).ainvoke(state["messages"])
        return {"messages": [response]}

    async def execute(state: RetrievalState) -> dict[str, Any]:
        raw_calls = getattr(state["messages"][-1], "tool_calls", None) or []
        calls = [_as_call(raw, index) for index, raw in enumerate(raw_calls)]
        finished = state["finished"] or any(call["name"] == FINISH_NAME for call in calls)
        question = _asked_question(calls) if not finished else ""
        if question:
            finished = True
        data_calls = [call for call in calls if call["name"] not in CONTROL_TOOL_NAMES]
        budget = state["tool_budget"]
        allowed = data_calls[:budget]
        skipped = data_calls[budget:]
        ran = await asyncio.gather(*[run_call(call["name"], call["args"], ctx) for call in allowed])
        records = [
            *ran,
            *(
                ToolCallRecord(name=call["name"], args=call["args"], ok=False, error="tool budget spent")
                for call in skipped
            ),
        ]
        update: dict[str, Any] = {
            "calls": [record.model_dump() for record in records],
            "tool_budget": budget - len(allowed),
            "finished": finished,
        }
        if question:
            update["question"] = question
        messages = [
            ToolMessage(content=_content(record), tool_call_id=call["id"], name=record.name)
            for call, record in zip([*allowed, *skipped], records, strict=True)
        ]
        if messages:
            update["messages"] = messages
        return update

    def pack(_: RetrievalState) -> dict[str, Any]:
        return {}

    graph = StateGraph(RetrievalState)
    graph.add_node("select", select)
    graph.add_node("execute", execute)
    graph.add_node("pack", pack)
    graph.set_entry_point("select")
    graph.add_conditional_edges("select", _route_after_select, {"execute": "execute", "pack": "pack"})
    graph.add_conditional_edges("execute", _route_after_execute, {"select": "select", "pack": "pack"})
    graph.add_edge("pack", END)
    return graph.compile()


async def retrieve(
    query: str,
    ctx: ToolContext,
    *,
    tool_budget: int = 8,
    model: Any | None = None,
    session: RetrievalSession | None = None,
) -> RetrievalResult:
    """Run the data tools the question needs and return their outputs.

    ``session`` holds the transcript only until this retrieval finishes. A question
    leaves it open so the parent or a human can answer. Finish, plain completion,
    and a spent budget close it and drop the messages.
    """
    return await run_agent(
        query,
        ctx,
        tools=model_tools(),
        run_call=run_data_call,
        system_prompt=retrieval_system_prompt(ctx.scenario_time),
        tool_budget=tool_budget,
        model=model,
        session=session,
    )


async def run_agent(
    query: str,
    ctx: ToolContext,
    *,
    tools: list[Any],
    run_call: RunCall,
    system_prompt: str,
    tool_budget: int,
    model: Any | None,
    session: RetrievalSession | None,
) -> RetrievalResult:
    """Select-execute loop shared by sub-agents that differ only in tools and prompt."""
    if tool_budget < 0:
        raise ValueError("tool_budget must be >= 0")
    if model is None:
        from garuda.harness.llm import chat_model

        model = chat_model()
    if session is None:
        session = RetrievalSession()
    graph = build_retrieval_graph(ctx, model, tools=tools, run_call=run_call)
    state = await graph.ainvoke(
        {
            "messages": _turn_messages(session, query, system_prompt),
            "calls": [],
            "tool_budget": tool_budget,
            "finished": False,
            "question": "",
        },
        config={"recursion_limit": tool_budget * 4 + 5},
    )
    question = _question_from_state(state)
    if question:
        session.keep(state["messages"])
    else:
        session.close()
    return RetrievalResult(
        query=query,
        calls=[ToolCallRecord.model_validate(call) for call in state["calls"]],
        question=question,
        session=session if question else None,
    )


def _route_after_select(state: RetrievalState) -> str:
    if getattr(state["messages"][-1], "tool_calls", None):
        return "execute"
    return "pack"


def _route_after_execute(state: RetrievalState) -> str:
    if state["finished"] or state["tool_budget"] <= 0:
        return "pack"
    return "select"


def _as_call(raw: Any, index: int) -> dict[str, Any]:
    if isinstance(raw, dict):
        name = str(raw.get("name", ""))
        args = raw.get("args") or {}
        call_id = str(raw.get("id") or f"call_{index}")
    else:
        name = str(getattr(raw, "name", ""))
        args = getattr(raw, "args", None) or {}
        call_id = str(getattr(raw, "id", "") or f"call_{index}")
    if not isinstance(args, dict):
        args = {}
    return {"name": name, "args": args, "id": call_id}


def _content(record: ToolCallRecord) -> str:
    if record.ok:
        return json.dumps(record.data)
    return json.dumps({"ok": False, "error": record.error})


def _asked_question(calls: list[dict[str, Any]]) -> str:
    asked = next((call for call in calls if call["name"] == ASK_NAME), None)
    if asked is None:
        return ""
    question = str(asked["args"].get("question") or "").strip()
    if question:
        return question
    return "Need more context before the data tools can run."


def _turn_messages(session: RetrievalSession, query: str, system_prompt: str) -> list[Any]:
    prior = session.take_prior()
    pending_id = _pending_ask_id(prior)
    if pending_id:
        return [*prior, ToolMessage(content=query, tool_call_id=pending_id, name=ASK_NAME)]
    messages: list[Any] = []
    if not any(isinstance(message, SystemMessage) for message in prior):
        messages.append(SystemMessage(content=system_prompt))
    messages.extend(prior)
    messages.append(HumanMessage(content=query))
    return messages


def _pending_ask_id(messages: list[Any]) -> str | None:
    answered = {message.tool_call_id for message in messages if isinstance(message, ToolMessage)}
    for message in reversed(messages):
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            parsed = _as_call(call, 0)
            if parsed["name"] == ASK_NAME and parsed["id"] not in answered:
                return parsed["id"]
        if tool_calls:
            return None
    return None


def _question_from_state(state: RetrievalState) -> str | None:
    stored = state.get("question", "").strip()
    if stored:
        return stored
    last = state["messages"][-1]
    if not isinstance(last, AIMessage) or getattr(last, "tool_calls", None):
        return None
    text = last.content if isinstance(last.content, str) else ""
    text = text.strip()
    return text or None
