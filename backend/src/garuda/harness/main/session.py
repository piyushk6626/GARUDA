"""In-memory state for one main-agent conversation.

There is no checkpointer. A run that waits for a human returns this object, and the
caller passes it back to ``resume_main``. close() drops every open sub-agent session.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langchain_core.messages import AnyMessage

from garuda.harness.main.schemas import Mode, StepOutcome, WorkPlan
from garuda.harness.retrieval.schemas import ToolCallRecord
from garuda.harness.retrieval.session import RetrievalSession

Phase = Literal["route", "agent", "discover", "approve", "execute", "clarify", "review", "respond", "closed"]
Awaiting = Literal["answer", "approval"]
ApprovalKind = Literal["initial", "revision"]
OnEvent = Callable[[dict[str, Any]], Awaitable[None]]


class MainSession:
    def __init__(
        self,
        query: str,
        *,
        model: Any,
        agents: dict[str, Any],
        on_event: OnEvent | None = None,
    ) -> None:
        self.query = query
        self.model = model
        self.agents = agents
        self.mode: Mode | None = None
        self.phase: Phase = "route"
        self.awaiting: Awaiting | None = None
        self.messages: list[AnyMessage] = []
        self.pending_ask_id: str | None = None
        self.children: dict[str, RetrievalSession] = {}
        self.plan: WorkPlan | None = None
        self.plan_history: list[WorkPlan] = []
        self.proposed: WorkPlan | None = None
        self.approval_kind: ApprovalKind = "initial"
        self.outcomes: list[StepOutcome] = []
        self.step_index = 0
        self.step_session: RetrievalSession | None = None
        self.step_results: list[dict[str, Any]] = []
        self.step_question: str | None = None
        self.step_reply: str | None = None
        self.clarify_rounds = 0
        self.retries: dict[str, int] = {}
        self.review_feedback = ""
        self.abort_reason = ""
        self.turn_calls: list[ToolCallRecord] = []
        self.open = True
        self.on_event = on_event

    def close(self) -> None:
        for child in self.children.values():
            child.close()
        self.children.clear()
        if self.step_session is not None:
            self.step_session.close()
            self.step_session = None
        self.messages.clear()
        self.pending_ask_id = None
        self.awaiting = None
        self.phase = "closed"
        self.open = False
