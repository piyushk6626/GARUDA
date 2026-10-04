"""HMAC approval tokens: the API signs on commander approval, commit_plan verifies.

Token format: `base64url(json(claims)).hex(HMAC-SHA256(secret, body))`.
"""

import base64
import hashlib
import hmac
import json
import os

from pydantic import BaseModel, ValidationError

SECRET_ENV = "GARUDA_APPROVAL_SECRET"


class ApprovalTokenError(PermissionError):
    """Missing, malformed, forged, mismatched, or expired approval token."""


class ApprovalClaims(BaseModel):
    plan_id: str
    approved_by: str
    expires_at: int


def approval_secret() -> bytes:
    secret = os.environ.get(SECRET_ENV, "")
    if not secret:
        raise ApprovalTokenError(f"{SECRET_ENV} is not set; refusing to verify approvals")
    return secret.encode()


def _signature(body: str, secret: bytes) -> str:
    return hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()


def sign_approval(claims: ApprovalClaims, secret: bytes) -> str:
    body = base64.urlsafe_b64encode(claims.model_dump_json().encode()).decode()
    return f"{body}.{_signature(body, secret)}"


def verify_approval(token: str, plan_id: str, secret: bytes, now: int) -> ApprovalClaims:
    parts = token.split(".")
    if len(parts) != 2:
        raise ApprovalTokenError("malformed approval token")
    body, signature = parts
    if not hmac.compare_digest(signature, _signature(body, secret)):
        raise ApprovalTokenError("approval token signature is invalid")
    try:
        claims = ApprovalClaims.model_validate(json.loads(base64.urlsafe_b64decode(body)))
    except (ValueError, ValidationError) as exc:
        raise ApprovalTokenError("approval token claims are unreadable") from exc
    if claims.plan_id != plan_id:
        raise ApprovalTokenError(f"approval token is for plan {claims.plan_id}, not {plan_id}")
    if now >= claims.expires_at:
        raise ApprovalTokenError("approval token has expired")
    return claims
