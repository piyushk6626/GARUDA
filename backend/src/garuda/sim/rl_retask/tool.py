import asyncio
import time
from uuid import uuid4

from garuda.contracts.plan import PlanDiff
from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.sim.rl_retask.decisions import decision_value, filled
from garuda.sim.rl_retask.explain import explain
from garuda.sim.rl_retask.model import load_policy, run_policy, solve_milp
from garuda.sim.rl_retask.problem import build_problem
from garuda.sim.rl_retask.schemas import RlRetaskInput, RlRetaskOutput

SPEC = ToolSpec(
    name="rl_retask",
    description="(Stretch) RL policy retasking, advisory only.",
    input_model=RlRetaskInput,
    output_model=RlRetaskOutput,
    side_effects="draft",
    timeout_s=20,
)

STUB_OUTPUT = RlRetaskOutput(
    new_plan_id="plan-stub",
    diff=PlanDiff(),
    policy_version="skeleton-0",
    decision_latency_ms=12.0,
    compare_with_milp={"value_gap": 0.0},
)


async def rl_retask(payload: RlRetaskInput, ctx: ToolContext) -> RlRetaskOutput:
    problem = build_problem(payload, ctx)
    policy, version = load_policy(ctx, payload.deterministic)
    started = time.perf_counter()
    decision = run_policy(problem.instance, policy)
    latency_ms = (time.perf_counter() - started) * 1000.0
    new_plan, diff = explain(problem, decision, f"{problem.plan.plan_id}-rl-{uuid4().hex[:8]}")
    new_plan_id = ctx.plans.put(new_plan) or new_plan.plan_id

    milp = await asyncio.to_thread(solve_milp, problem.instance)
    compare = None
    if milp is not None:
        rl_value = decision_value(problem.instance, decision)
        milp_value = decision_value(problem.instance, milp)
        compare = {
            "rl_value": rl_value,
            "milp_value": milp_value,
            "value_gap": milp_value - rl_value,
            "rl_filled": float(filled(decision)),
            "milp_filled": float(filled(milp)),
            "slots": float(len(decision)),
        }
    return RlRetaskOutput(
        new_plan_id=new_plan_id,
        diff=diff,
        policy_version=version,
        decision_latency_ms=latency_ms,
        compare_with_milp=compare,
    )


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, rl_retask)
