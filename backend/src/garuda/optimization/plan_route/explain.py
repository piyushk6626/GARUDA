"""Convert a validated solution to the frozen route output."""

import math
from datetime import timedelta

from garuda.contracts.geo import Waypoint
from garuda.optimization.plan_route.problem import GEOD
from garuda.optimization.plan_route.schemas import PlanRouteOutput, RouteSummary


def cumulative_risk(problem, mask):
    return 1 - math.prod(1 - t.probability for i, t in enumerate(problem.threats)
                         if mask & (1 << i))


def explain(problem, solution) -> PlanRouteOutput:
    route = solution.route
    latlon = problem.latlon(route.points)
    # Preserve the exact public endpoints, avoiding projection round-off.
    latlon[0] = problem.payload.origin.lat, problem.payload.origin.lon
    latlon[-1] = problem.payload.destination.lat, problem.payload.destination.lon
    waypoints, distance = [], 0.0
    previous = None
    for lat, lon in latlon:
        if previous is not None:
            distance += abs(GEOD.inv(previous[1], previous[0], lon, lat)[2]) / 1000
        eta = problem.payload.depart_time + timedelta(hours=distance / (problem.aircraft.cruise_kts * 1.852))
        waypoints.append(Waypoint(lat=float(lat), lon=float(lon), alt_ft=route.altitude, eta=eta))
        previous = lat, lon
    return PlanRouteOutput(
        waypoints=waypoints,
        distance_km=distance,
        fuel_kg=distance / problem.aircraft.range_km * problem.aircraft.fuel_capacity_kg,
        cumulative_risk=cumulative_risk(problem, route.mask),
        airspace_crossed=list(route.zones),
        alternatives=[RouteSummary(label=label, distance_km=alternative.distance,
                                   cumulative_risk=cumulative_risk(problem, alternative.mask))
                      for label, alternative in solution.alternatives],
    )
