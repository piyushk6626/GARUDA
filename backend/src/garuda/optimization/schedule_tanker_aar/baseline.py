"""Greedy nearest-window heuristic: fallback, test oracle, gap comparison."""

from dataclasses import dataclass, field

from garuda.optimization.schedule_tanker_aar.problem import AarProblem, classify_unserved, evaluate_route


@dataclass
class Stop:
    demand_idx: int
    aarp_idx: int
    time_min: float
    offload_kg: float


@dataclass
class Solution:
    routes: list[list[Stop]]
    unserved: list[tuple[int, str]] = field(default_factory=list)
    travel_min: float = 0.0
    method: str = "greedy"


def build_solution(problem: AarProblem, plans: list[list[tuple[int, int]]], method: str) -> Solution:
    """Re-time each tanker's ordered (demand, aarp) stops; unserved demands are the ones left over."""
    routes: list[list[Stop]] = []
    travel_total = 0.0
    served: set[int] = set()
    for tanker_idx, stops in enumerate(plans):
        evaluated = evaluate_route(problem, tanker_idx, stops)
        if evaluated is None:
            raise ValueError(f"infeasible route for {problem.tankers[tanker_idx].tail}")
        times, travel = evaluated
        travel_total += travel
        served.update(d for d, _ in stops)
        routes.append([Stop(d, a, t, problem.demands[d].fuel_kg) for (d, a), t in zip(stops, times)])
    unserved = [(i, classify_unserved(problem, i)) for i in range(len(problem.demands)) if i not in served]
    return Solution(routes, unserved, travel_total, method)


def baseline(problem: AarProblem) -> Solution:
    plans: list[list[tuple[int, int]]] = [[] for _ in problem.tankers]
    order = sorted(range(len(problem.demands)), key=lambda i: problem.demands[i].start_min)
    for demand_idx in order:
        best: tuple[float, int, int] | None = None
        for tanker_idx, stops in enumerate(plans):
            before = evaluate_route(problem, tanker_idx, stops)
            if before is None:
                continue
            for aarp_idx in problem.demands[demand_idx].aarp_idx:
                after = evaluate_route(problem, tanker_idx, stops + [(demand_idx, aarp_idx)])
                if after is None:
                    continue
                added = after[1] - before[1]
                if best is None or added < best[0]:
                    best = (added, tanker_idx, aarp_idx)
        if best is not None:
            plans[best[1]].append((demand_idx, best[2]))
    return build_solution(problem, plans, "greedy")
