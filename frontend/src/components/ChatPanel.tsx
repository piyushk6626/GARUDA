import { useEffect, useReducer, useRef, useState } from "react";

import { streamChat } from "../api";
import type { ChatEvent, WorkPlan } from "../types";

type Line = { id: number; text: string; tone?: "ok" | "bad" };
type Turn =
  | { id: number; kind: "user"; text: string }
  | { id: number; kind: "trace"; lines: Line[]; live: boolean }
  | { id: number; kind: "plan"; plan: WorkPlan; pending: boolean }
  | { id: number; kind: "question"; text: string }
  | { id: number; kind: "assistant"; text: string; status: string }
  | { id: number; kind: "error"; text: string };

type Wait = "idle" | "approval" | "answer";
type State = { turns: Turn[]; wait: Wait; streaming: boolean };
type Action =
  | { type: "user"; text: string }
  | { type: "event"; event: ChatEvent }
  | { type: "fail"; text: string }
  | { type: "settle-plan" };

let seq = 0;
const nextId = () => ++seq;

function addLine(state: State, text: string, tone?: Line["tone"]): State {
  const turns = [...state.turns];
  const last = turns[turns.length - 1];
  const line = { id: nextId(), text, tone };
  if (last?.kind === "trace" && last.live) {
    turns[turns.length - 1] = { ...last, lines: [...last.lines, line] };
    return { ...state, turns };
  }
  return { ...state, turns: [...turns, { id: nextId(), kind: "trace", lines: [line], live: true }] };
}

function freeze(state: State): State {
  return { ...state, turns: state.turns.map((turn) => (turn.kind === "trace" ? { ...turn, live: false } : turn)) };
}

function reduce(state: State, action: Action): State {
  if (action.type === "user") {
    return {
      ...state,
      streaming: true,
      wait: "idle",
      turns: [...freeze(state).turns, { id: nextId(), kind: "user", text: action.text }, { id: nextId(), kind: "trace", lines: [], live: true }],
    };
  }
  if (action.type === "fail") {
    return { ...freeze(state), streaming: false, wait: "idle", turns: [...freeze(state).turns, { id: nextId(), kind: "error", text: action.text }] };
  }
  if (action.type === "settle-plan") {
    return { ...state, turns: state.turns.map((turn) => (turn.kind === "plan" ? { ...turn, pending: false } : turn)) };
  }
  const event = action.event;
  if (event.type === "session") return state;
  if (event.type === "phase") return addLine(state, `Phase · ${event.phase}`);
  if (event.type === "tool_start") return addLine(state, toolText(event.name, event.args));
  if (event.type === "tool_end") {
    return addLine(state, event.ok ? `${event.name} finished` : `${event.name} failed: ${event.error || "error"}`, event.ok ? "ok" : "bad");
  }
  if (event.type === "step_start") return addLine(state, `Step ${event.step_id} · ${event.agent}: ${event.objective}`);
  if (event.type === "step_end") return addLine(state, `Step ${event.step_id} ${event.status}`, event.status === "done" ? "ok" : event.status === "failed" || event.status === "blocked" ? "bad" : undefined);
  if (event.type === "plan_proposed" && event.plan) {
    return { ...freeze(state), turns: [...freeze(state).turns, { id: nextId(), kind: "plan", plan: event.plan, pending: true }] };
  }
  if (event.type === "question") {
    return { ...freeze(state), turns: [...freeze(state).turns, { id: nextId(), kind: "question", text: event.question }] };
  }
  if (event.type === "final") {
    return { ...freeze(state), turns: [...freeze(state).turns, { id: nextId(), kind: "assistant", text: event.answer, status: event.status }] };
  }
  if (event.type === "error") {
    return { ...freeze(state), streaming: false, wait: "idle", turns: [...freeze(state).turns, { id: nextId(), kind: "error", text: event.error }] };
  }
  if (event.type === "done") {
    const wait: Wait = event.status === "awaiting_approval" ? "approval" : event.status === "awaiting_answer" ? "answer" : "idle";
    return { ...freeze(state), streaming: false, wait };
  }
  return state;
}

function toolText(name: string, args: Record<string, unknown>) {
  const query = typeof args.query === "string" ? args.query : "";
  const short = query.length > 90 ? `${query.slice(0, 90)}…` : query;
  return short ? `Calling ${name}: ${short}` : `Calling ${name}`;
}

const initial: State = { turns: [], wait: "idle", streaming: false };

export function ChatPanel() {
  const [mode, setMode] = useState<"agent" | "plan">("agent");
  const [text, setText] = useState("");
  const [feedback, setFeedback] = useState("");
  const [state, dispatch] = useReducer(reduce, initial);
  const sessionId = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const canChangeMode = !state.streaming && state.wait === "idle";

  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [state.turns, state.streaming]);

  async function run(url: string, body: unknown) {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    try {
      await streamChat(url, body, (event) => {
        if (event.type === "session") sessionId.current = event.session_id;
        dispatch({ type: "event", event });
      }, controller.signal);
    } catch (error) {
      if (controller.signal.aborted) return;
      dispatch({ type: "fail", text: error instanceof Error ? error.message : "The request failed." });
    }
  }

  function send() {
    const query = text.trim();
    if (!query || state.streaming || state.wait === "approval") return;
    setText("");
    dispatch({ type: "user", text: query });
    if (state.wait === "answer" && sessionId.current) {
      void run(`/api/chat/${sessionId.current}/resume`, { answer: query });
      return;
    }
    void run("/api/chat", { query, mode });
  }

  function decide(approved: boolean) {
    if (!sessionId.current || state.streaming) return;
    dispatch({ type: "settle-plan" });
    dispatch({ type: "user", text: approved ? "Approved the plan." : `Rejected the plan. ${feedback}`.trim() });
    void run(`/api/chat/${sessionId.current}/resume`, { approved, feedback });
    setFeedback("");
  }

  return (
    <aside className="chat">
      <div className="chat-head">
        <div>
          <strong>Commander</strong>
          <em>{mode === "plan" ? "Planning asks for approval, then runs each step." : "Regular answers from the agents directly."}</em>
        </div>
        <div className="mode" role="group" aria-label="Chat mode">
          <button type="button" className={mode === "agent" ? "on" : ""} disabled={!canChangeMode} onClick={() => setMode("agent")}>Regular</button>
          <button type="button" className={mode === "plan" ? "on" : ""} disabled={!canChangeMode} onClick={() => setMode("plan")}>Planning</button>
        </div>
      </div>
      <div className="transcript">
        {state.turns.length === 0 ? <p className="empty">Ask about the scenario. Switch to Planning when the request needs a work plan.</p> : null}
        {state.turns.map((turn) => <TurnView key={turn.id} turn={turn} />)}
        <div ref={end} />
      </div>
      {state.wait === "approval" ? (
        <form className="composer" onSubmit={(event) => { event.preventDefault(); decide(false); }}>
          <textarea value={feedback} placeholder="Feedback if you reject the plan" onChange={(event) => setFeedback(event.target.value)} />
          <div className="composer-actions">
            <button type="button" className="primary" onClick={() => decide(true)}>Approve</button>
            <button type="submit">Reject</button>
          </div>
        </form>
      ) : (
        <form className="composer" onSubmit={(event) => { event.preventDefault(); send(); }}>
          <textarea
            value={text}
            placeholder={state.wait === "answer" ? "Answer the question" : "Ask GARUDA"}
            disabled={state.streaming}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                send();
              }
            }}
          />
          <div className="composer-actions">
            <button type="submit" className="primary" disabled={state.streaming || !text.trim()}>{state.wait === "answer" ? "Answer" : "Send"}</button>
          </div>
        </form>
      )}
    </aside>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  if (turn.kind === "user") return <div className="bubble user">{turn.text}</div>;
  if (turn.kind === "assistant") return <div className={`bubble assistant ${turn.status}`}>{turn.text}</div>;
  if (turn.kind === "error") return <div className="bubble error">{turn.text}</div>;
  if (turn.kind === "question") return <div className="bubble question"><b>Question</b><p>{turn.text}</p></div>;
  if (turn.kind === "plan") return <PlanCard plan={turn.plan} pending={turn.pending} />;
  if (!turn.lines.length) return null;
  return (
    <ol className={`trace${turn.live ? " live" : ""}`}>
      {turn.lines.map((line) => <li key={line.id} className={line.tone || ""}>{line.text}</li>)}
    </ol>
  );
}

function PlanCard({ plan, pending }: { plan: WorkPlan; pending: boolean }) {
  return (
    <section className={`plan-card${pending ? " pending" : ""}`}>
      <h2>Plan v{plan.version}{pending ? " · waiting for you" : ""}</h2>
      <p>{plan.goal}</p>
      <ol>
        {plan.steps.map((step) => (
          <li key={step.id}>
            <b>{step.id}</b> {step.agent}
            <span>{step.objective}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
