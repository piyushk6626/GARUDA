import asyncio
import hashlib
import hmac
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from garuda.contracts.plan import Assignment, PlanDraft
from garuda.contracts.tool import ToolInputError
from garuda.tools.commit.approval import (
    SECRET_ENV,
    ApprovalClaims,
    ApprovalTokenError,
    sign_approval,
    verify_approval,
)
from garuda.tools.commit.commit_plan import CommitPlanInput, commit_plan

T0 = datetime(2026, 10, 3, tzinfo=timezone.utc)
SECRET = b"test-secret"
NOW = 1_800_000_000


class PlanStore:
    def __init__(self, *plans: PlanDraft) -> None:
        self.plans = {p.plan_id: p for p in plans}
        self.puts = 0

    def get(self, plan_id: str) -> PlanDraft:
        return self.plans[plan_id]

    def put(self, draft: PlanDraft) -> str:
        self.puts += 1
        self.plans[draft.plan_id] = draft
        return draft.plan_id


def make_plan(status: str = "APPROVED", plan_id: str = "P1") -> PlanDraft:
    return PlanDraft(
        plan_id=plan_id,
        scenario_time=T0,
        status=status,
        assignments=[Assignment(tail_no="SU-1", config_id="CFG-1", mission_id="M1", depart=T0, tot=T0)],
    )


def token(plan_id: str = "P1", expires_at: int = NOW + 600, secret: bytes = SECRET) -> str:
    claims = ApprovalClaims(plan_id=plan_id, approved_by="cdr-1", expires_at=expires_at)
    return sign_approval(claims, secret)


def run(store: PlanStore, approval_token: str, plan_id: str = "P1"):
    payload = CommitPlanInput(plan_id=plan_id, approval_token=approval_token)
    return asyncio.run(commit_plan(payload, SimpleNamespace(plans=store)))


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv(SECRET_ENV, SECRET.decode())


def live_token(plan_id: str = "P1") -> str:
    return token(plan_id, expires_at=int(time.time()) + 600)


def test_sign_verify_round_trip():
    claims = verify_approval(token(), "P1", SECRET, NOW)
    assert claims == ApprovalClaims(plan_id="P1", approved_by="cdr-1", expires_at=NOW + 600)


def _tamper_body(raw: str) -> str:
    forged = sign_approval(ApprovalClaims(plan_id="P1", approved_by="llm", expires_at=NOW + 600), b"other")
    return f"{forged.split('.')[0]}.{raw.split('.')[1]}"


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(_tamper_body(token()), id="tampered-body"),
        pytest.param(token()[:-1] + ("0" if token()[-1] != "0" else "1"), id="tampered-signature"),
        pytest.param(token(secret=b"wrong"), id="wrong-secret"),
        pytest.param(token(plan_id="P2"), id="wrong-plan"),
        pytest.param(token(expires_at=NOW), id="expired"),
        pytest.param("garbage", id="no-dot"),
        pytest.param("a.b.c", id="too-many-parts"),
        pytest.param("", id="empty"),
    ],
)
def test_verify_rejects(bad):
    with pytest.raises(ApprovalTokenError):
        verify_approval(bad, "P1", SECRET, NOW)


@pytest.mark.parametrize("body", ["bm90LWpzb24=", "WzFd", "!!!"], ids=["not-json", "json-list", "not-base64"])
def test_verify_rejects_signed_garbage_body(body):
    signed = f"{body}.{hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()}"
    with pytest.raises(ApprovalTokenError, match="unreadable"):
        verify_approval(signed, "P1", SECRET, NOW)


def test_commit_marks_plan_committed(secret_env):
    original = make_plan("APPROVED")
    store = PlanStore(original)

    out = run(store, live_token())

    assert out.committed and out.plan_id == "P1" and out.approved_by == "cdr-1"
    stored = store.get("P1")
    assert stored.status == "COMMITTED"
    assert stored.model_dump(exclude={"status"}) == original.model_dump(exclude={"status"})


def test_commit_accepts_awaiting_approval(secret_env):
    store = PlanStore(make_plan("AWAITING_APPROVAL"))
    assert run(store, live_token()).committed
    assert store.get("P1").status == "COMMITTED"


def test_commit_is_idempotent(secret_env):
    store = PlanStore(make_plan("COMMITTED"))
    out = run(store, live_token())
    assert out.committed
    assert store.puts == 0


@pytest.mark.parametrize("status", ["DRAFT", "REJECTED"])
def test_commit_refuses_uncommittable_status(secret_env, status):
    store = PlanStore(make_plan(status))
    with pytest.raises(ToolInputError, match=status):
        run(store, live_token())
    assert store.get("P1").status == status
    assert store.puts == 0


def test_invalid_token_leaves_store_untouched(secret_env):
    store = PlanStore(make_plan("APPROVED"))
    with pytest.raises(ApprovalTokenError):
        run(store, live_token(plan_id="P2"))
    assert store.get("P1").status == "APPROVED"
    assert store.puts == 0


def test_missing_secret_fails_closed(monkeypatch):
    monkeypatch.delenv(SECRET_ENV, raising=False)
    store = PlanStore(make_plan("APPROVED"))
    with pytest.raises(ApprovalTokenError, match=SECRET_ENV):
        run(store, live_token())
    assert store.puts == 0
