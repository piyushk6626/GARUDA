"""One-page app API: scenario payload and a streaming chat run."""

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from garuda.config import load_config
from garuda.generators.missions import build_scenario
from garuda.harness.main import PlanApproval, resume_main, run_main
from garuda.harness.main.session import MainSession
from garuda.viz import scenario_payload

from garuda.api.context import build_context

logger = logging.getLogger("garuda.api")

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG = REPO_ROOT / "config" / "scenario.yaml"
DEFAULT_MODELS = REPO_ROOT / "data" / "models"


class ChatRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: Literal["plan", "agent"] = "agent"


class ResumeRequest(BaseModel):
    answer: str | None = None
    approved: bool | None = None
    feedback: str = ""


def create_app(*, scenario: Any = None, ctx: Any = None, model: Any = None, sub_agents: Any = None) -> FastAPI:
    """App factory. Tests pass a scenario, context, and scripted model; the server builds them."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if scenario is None:
            config_path = Path(os.environ.get("GARUDA_CONFIG", DEFAULT_CONFIG))
            config = load_config(config_path)
            seed = int(os.environ.get("GARUDA_SEED", config.seed))
            built = build_scenario(config, seed)
            app.state.scenario = built
            app.state.ctx = build_context(
                built,
                scenario_time=config.start,
                seed=seed,
                model_dir=Path(os.environ.get("GARUDA_MODEL_DIR", DEFAULT_MODELS)),
            )
        else:
            app.state.scenario = scenario
            app.state.ctx = ctx
        app.state.model = model
        app.state.sub_agents = sub_agents
        app.state.sessions = {}
        yield
        app.state.sessions.clear()

    app = FastAPI(title="GARUDA", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/scenario")
    def get_scenario(request: Request) -> dict[str, Any]:
        return scenario_payload(request.app.state.scenario)

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
        session_id = uuid.uuid4().hex

        async def run(on_event):
            return await run_main(
                body.query,
                request.app.state.ctx,
                mode=body.mode,
                model=request.app.state.model,
                sub_agents=request.app.state.sub_agents,
                on_event=on_event,
            )

        return _stream(request, session_id, run)

    @app.post("/api/chat/{session_id}/resume")
    async def resume(session_id: str, body: ResumeRequest, request: Request) -> StreamingResponse:
        session = _waiting_session(request, session_id)
        answer, approval = _resume_input(session, body)

        async def run(on_event):
            return await resume_main(
                session,
                request.app.state.ctx,
                answer=answer,
                approval=approval,
                model=request.app.state.model,
                on_event=on_event,
            )

        return _stream(request, session_id, run)

    return app


def _waiting_session(request: Request, session_id: str) -> MainSession:
    session = request.app.state.sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    if not session.open or session.awaiting is None:
        raise HTTPException(status_code=409, detail="session is not waiting for input")
    return session


def _resume_input(session: MainSession, body: ResumeRequest) -> tuple[str | None, PlanApproval | None]:
    if session.awaiting == "answer":
        if body.answer is None:
            raise HTTPException(status_code=422, detail="session is waiting for an answer")
        return body.answer, None
    if body.approved is None:
        raise HTTPException(status_code=422, detail="session is waiting for plan approval")
    return None, PlanApproval(approved=body.approved, feedback=body.feedback)


def _stream(request: Request, session_id: str, run) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        yield _frame({"type": "session", "session_id": session_id})
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def on_event(event: dict[str, Any]) -> None:
            await queue.put(event)

        async def worker() -> None:
            try:
                result = await run(on_event)
                if result.session is not None:
                    request.app.state.sessions[session_id] = result.session
                await queue.put({"type": "done", "status": result.status})
            except Exception as exc:
                logger.exception("chat run failed")
                await queue.put({"type": "error", "error": str(exc)})
            finally:
                await queue.put(None)

        task = asyncio.create_task(worker())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _frame(event)
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _frame(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


app = create_app()
