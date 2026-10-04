"""explain(problem, solution) -> Explanation. See docs/03_optimization_layer/vrp_tanker_aar_scheduling.md."""

from dataclasses import dataclass

from garuda.optimization.schedule_tanker_aar.baseline import Solution
from garuda.optimization.schedule_tanker_aar.problem import AarProblem


@dataclass
class Explanation:
    spare_fuel_kg: dict[str, float]
    unserved_missions: list[str]
    summary: str


def explain(problem: AarProblem, solution: Solution) -> Explanation:
    spare = {
        tanker.tail: tanker.capacity_kg - sum(stop.offload_kg for stop in route)
        for tanker, route in zip(problem.tankers, solution.routes)
    }
    missing = [problem.demands[i].mission_id for i, _ in solution.unserved]
    missing += [mission_id for mission_id, _, _, _ in problem.pre_unserved]
    served = sum(len(route) for route in solution.routes)
    summary = f"{solution.method}: served {served} refuelling events, {len(missing)} unserved"
    if missing:
        summary += f" ({', '.join(missing)}); these missions cannot rely on AAR"
    return Explanation(spare, missing, summary)
