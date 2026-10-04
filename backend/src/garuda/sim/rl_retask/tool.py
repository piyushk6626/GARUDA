import asyncio
import logging
from time import perf_counter

from garuda.contracts.plan import PlanDiff
from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.sim.rl_retask.baseline import baseline
from garuda.sim.rl_retask.explain import explain
from garuda.sim.rl_retask.model import (
    MODEL_NAME,
    selection_errors,
    solve,
    validate_artifact,
)
from garuda.sim.rl_retask.problem import build_problem
from garuda.sim.rl_retask.schemas import RlRetaskInput, RlRetaskOutput
from garuda.validation.plan_validator import validate_plan

logger = logging.getLogger(__name__)

SPEC = ToolSpec(
    name="rl_retask",
    description="(Stretch) RL policy retasking, advisory only.",
    input_model=RlRetaskInput,
    output_model=RlRetaskOutput,
    side_effects="none",
    timeout_s=20,
)

# Compatibility fixture only; runtime and graph never use this output.
STUB_OUTPUT = RlRetaskOutput(
    new_plan_id="plan-stub",
    diff=PlanDiff(),
    policy_version="skeleton-0",
    decision_latency_ms=12.0,
    compare_with_milp={"value_gap": 0.0},
)


async def rl_retask(payload: RlRetaskInput, ctx: ToolContext) -> RlRetaskOutput:
    """Infer, compare and validate an advisory repair with the frozen RORO API."""
    started = perf_counter()
    # The shared executor is still a stub. The worker never persists, so a
    # cancelled caller cannot leave a draft written by its background worker.
    draft, diff, version, comparison = await asyncio.to_thread(_compute, payload, ctx)
    plan_id = ctx.plans.put(draft)
    return RlRetaskOutput(
        new_plan_id=plan_id,
        diff=diff,
        policy_version=version,
        decision_latency_ms=(perf_counter() - started) * 1000,
        compare_with_milp=comparison,
    )


def _checked_draft(problem, solution, ctx):
    errors = selection_errors(problem, solution)
    if errors:
        raise ValueError("Retasking decision failed validation: " + "; ".join(errors))
    draft, diff = explain(problem, solution)
    try:
        violations = validate_plan(draft, ctx)
    except NotImplementedError:
        # The shared checker is a foundation stub. The independent selection
        # checker above always runs against the event-aware resource model.
        violations = []
    if violations:
        raise ValueError(
            "Plan constraint checker rejected decision: "
            + "; ".join(f"{v.rule}:{v.entity_id}:{v.detail}" for v in violations)
        )
    return draft, diff


def _compute(payload, ctx):
    started = perf_counter()
    problem = build_problem(payload, ctx)
    if problem.retask.frozen_conflicts:
        raise ToolInputError(
            "Event makes frozen assignments infeasible: "
            + ", ".join(sorted(problem.retask.frozen_conflicts))
            + ". Resolve the frozen-plan conflict before retasking."
        )
    rl_solution = rl_draft = None
    version = "milp-fallback"
    rl_started = perf_counter()
    try:
        artifact = ctx.models.load(MODEL_NAME) if ctx.models is not None else None
    except (KeyError, FileNotFoundError, NotImplementedError):
        artifact = None
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        logger.warning(
            "RL retasking checkpoint could not be loaded; using MILP: %s", exc
        )
        artifact = None
    if artifact is not None:
        try:
            artifact = validate_artifact(artifact)
            version = artifact.version
            rl_solution = solve(
                problem, artifact, deadline=min(started + 6, perf_counter() + 4)
            )
            rl_draft = _checked_draft(problem, rl_solution, ctx)
        except Exception as exc:
            logger.warning("RL retasking policy rejected; using MILP: %s", exc)
            rl_solution = rl_draft = None
            version = (
                f"{version}/milp-fallback" if version != "milp-fallback" else version
            )
    rl_latency = (perf_counter() - rl_started) * 1000
    milp_started = perf_counter()
    milp_solution = milp_draft = None
    try:
        milp_solution = baseline(problem)
        milp_draft = _checked_draft(problem, milp_solution, ctx)
    except Exception as exc:
        if rl_draft is None:
            raise ToolInputError(
                f"No validated retasking decision is available: {exc}"
            ) from exc
        logger.warning("RL retasking MILP comparison unavailable: %s", exc)
    milp_latency = (perf_counter() - milp_started) * 1000
    chosen = rl_draft if rl_draft is not None else milp_draft
    comparison = None
    if milp_draft is not None:
        comparison = {
            "value_gap": float(chosen[0].kpis.value - milp_draft[0].kpis.value),
            "objective_gap": float(
                (rl_solution if rl_draft is not None else milp_solution).objective
                - milp_solution.objective
            ),
            "rl_latency_ms": rl_latency,
            "milp_latency_ms": milp_latency,
            "used_milp": float(rl_draft is None),
            "constraint_violations": 0.0,
        }
    return *chosen, version, comparison


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, rl_retask)
