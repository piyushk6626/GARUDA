"""solve(problem, options) -> Solution: OR-Tools VRPTW, pure, sync, picklable inputs."""

import math

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from garuda.contracts.solve import SolveOptions
from garuda.optimization.schedule_tanker_aar.baseline import Solution, baseline, build_solution
from garuda.optimization.schedule_tanker_aar.problem import (
    SERVICE_MIN,
    AarProblem,
    arc_blocked,
    leg_min,
)

FORBIDDEN_MIN = 100_000
UNSERVED_PENALTY = 100_000
VEHICLE_FIXED_COST = 50


def _route_plans(problem: AarProblem, time_limit_s: int) -> list[list[tuple[int, int]]] | None:
    tankers = problem.tankers
    n_veh = len(tankers)
    pairs = [(d, a) for d, demand in enumerate(problem.demands) for a in demand.aarp_idx]
    n_nodes = n_veh + len(pairs)
    places = [(t.lat, t.lon) for t in tankers] + [
        (problem.aarps[a].lat, problem.aarps[a].lon) for _, a in pairs
    ]
    manager = pywrapcp.RoutingIndexManager(n_nodes, n_veh, list(range(n_veh)), list(range(n_veh)))
    routing = pywrapcp.RoutingModel(manager)

    blocked: dict[tuple[int, int], bool] = {}

    def is_blocked(a: int, b: int) -> bool:
        key = (a, b)
        if key not in blocked:
            blocked[key] = arc_blocked(problem, places[a], places[b])
        return blocked[key]

    def make_transit(vehicle: int):
        tanker = tankers[vehicle]

        def transit(i: int, j: int) -> int:
            a, b = manager.IndexToNode(i), manager.IndexToNode(j)
            cost = math.ceil(leg_min(tanker, places[a], places[b]))
            cost += int(SERVICE_MIN) if a >= n_veh else 0
            return cost + (FORBIDDEN_MIN if is_blocked(a, b) else 0)

        return routing.RegisterTransitCallback(transit)

    callbacks = [make_transit(v) for v in range(n_veh)]
    horizon = math.ceil(max(d.end_min for d in problem.demands)) + 24 * 60
    routing.AddDimensionWithVehicleTransitAndCapacity(callbacks, horizon, [horizon] * n_veh, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")
    for v in range(n_veh):
        routing.SetArcCostEvaluatorOfVehicle(callbacks[v], v)
        routing.SetFixedCostOfVehicle(VEHICLE_FIXED_COST, v)
        time_dim.SetSpanUpperBoundForVehicle(int(tankers[v].endurance_min), v)

    def fuel(i: int) -> int:
        node = manager.IndexToNode(i)
        return 0 if node < n_veh else math.ceil(problem.demands[pairs[node - n_veh][0]].fuel_kg)

    fuel_cb = routing.RegisterUnaryTransitCallback(fuel)
    routing.AddDimensionWithVehicleCapacity(
        fuel_cb, 0, [int(t.capacity_kg) for t in tankers], True, "Fuel"
    )

    groups: dict[int, list[int]] = {}
    for offset, (demand_idx, _) in enumerate(pairs):
        node = n_veh + offset
        index = manager.NodeToIndex(node)
        demand = problem.demands[demand_idx]
        time_dim.CumulVar(index).SetRange(math.ceil(demand.start_min), math.floor(demand.end_min))
        groups.setdefault(demand_idx, []).append(index)
    for demand_idx, indices in groups.items():
        penalty = UNSERVED_PENALTY + int(problem.demands[demand_idx].fuel_kg)
        routing.AddDisjunction(indices, penalty, 1)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = max(1, time_limit_s)
    assignment = routing.SolveWithParameters(params)
    if assignment is None:
        return None

    plans: list[list[tuple[int, int]]] = []
    for v in range(n_veh):
        stops: list[tuple[int, int]] = []
        index = assignment.Value(routing.NextVar(routing.Start(v)))
        while not routing.IsEnd(index):
            stops.append(pairs[manager.IndexToNode(index) - n_veh])
            index = assignment.Value(routing.NextVar(index))
        plans.append(stops)
    return plans


def solve(problem: AarProblem, options: SolveOptions | None = None) -> Solution:
    if not problem.tankers or not problem.demands:
        return build_solution(problem, [[] for _ in problem.tankers], "empty")
    limit = options.time_limit_s if options is not None else problem.time_limit_s
    plans = _route_plans(problem, limit)
    if plans is not None:
        try:
            return build_solution(problem, plans, "ortools")
        except ValueError:
            pass
    return baseline(problem)
