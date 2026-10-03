# Guardrails and Human-in-the-Loop

## Principle
GARUDA is decision **support**. No tasking change takes effect without explicit human approval, and every step is auditable.

## Controls
| Area | Control |
|---|---|
| Data access | Read-only DB roles for harness; repositories only; RBAC by user role (Planner, Commander, Observer) |
| Tool use | Allow-list per role; max tool calls per turn; per-tool timeouts; JSON-schema validation of args |
| Side effects | `draft` tools write only to `plan_draft`; `commit_plan` needs approval token signed by the API |
| Prompt injection | Retrieved text (docs, remarks fields) is delimited and treated as data; the system prompt forbids following instructions inside; tool args validated against schemas regardless |
| Hallucination | Numbers must come from tools; Critic checks; "insufficient data" is an allowed answer |
| Staleness | Each tool result has `data_as_of`; warn if older than threshold (default 15 min for status, 3 h for weather) |
| Solver safety | Always verify solver output against constraints independently (`validate_plan`) before showing |
| Data sensitivity | Cloud LLM receives only synthetic data in this prototype; add a redaction layer interface for later |

## Approval flow
1. Agent produces `PlanDraft` + KPIs + risks + alternatives (Pareto options).
2. LangGraph `interrupt()` -> API returns `status=awaiting_approval`.
3. Commander calls `POST /plans/{id}/approve` or `/reject` with comment.
4. Approved: commit; rejected: comment goes back to Planner as feedback.

## Audit log
Table `audit_event(id, ts, user, session_id, event_type, tool, args_hash, args, result_summary, plan_id, decision)`; append-only (revoke UPDATE/DELETE). Includes LLM model/version, prompts hash, solver version and seed.

## Explainability output
Every plan includes: objective value breakdown, binding constraints, unassigned missions with reason codes (`NO_QUALIFIED_CREW`, `NO_WEAPON_STOCK`, `WEATHER`, `THREAT_RISK`), sensitivity ("if tanker T2 is lost, loss of N points"), and data timestamps.

## Failure handling
Solver infeasible -> run relaxation analysis, report minimal set of constraints to relax; LLM tool error -> one retry with the error message; timeout -> return best incumbent flagged `FEASIBLE_NOT_OPTIMAL`.

## Testing
Red-team prompt set (injection, out-of-scope tool use), deterministic replay of recorded sessions with a mock LLM.
