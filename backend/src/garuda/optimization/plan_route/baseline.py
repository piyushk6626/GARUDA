"""Straight-line feasibility oracle; uses the same complete segment checks."""

from garuda.optimization.plan_route.model import Surface, assess


def baseline(problem):
    candidates = []
    for altitude in problem.altitudes:
        surface = Surface(problem, altitude, problem.payload.weights)
        points = [(0.0, 0.0)] if problem.goal == (0.0, 0.0) else [(0.0, 0.0), problem.goal]
        route = assess(surface, points)
        if route is not None:
            candidates.append(route)
    return min(candidates, key=lambda r: (r.cost, r.distance, r.altitude), default=None)
