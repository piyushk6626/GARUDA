"""build_problem(payload, ctx) -> AarProblem (data gathering only) plus shared route evaluation."""

from dataclasses import dataclass, field
from datetime import datetime

from shapely import wkt as shapely_wkt

from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.optimization.common.geo import haversine_km, segment_hits_circle, travel_time_min
from garuda.optimization.schedule_tanker_aar.schemas import ScheduleTankerAarInput

SERVICE_MIN = 10.0
RESERVE_FRACTION = 0.8
MAX_AARP_PER_DEMAND = 5
TANKER_CAPABILITY = "AAR_TANKER"
TANKER_STATUSES = {"FMC", "PMC"}

Point = tuple[float, float]


@dataclass(frozen=True)
class Aarp:
    aarp_id: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Tanker:
    tail: str
    lat: float
    lon: float
    capacity_kg: float
    endurance_min: float
    speed_kts: float


@dataclass(frozen=True)
class Demand:
    mission_id: str
    tail_no: str
    fuel_kg: float
    start_min: float
    end_min: float
    lat: float
    lon: float
    aarp_idx: tuple[int, ...] = ()


@dataclass(frozen=True)
class Threat:
    lat: float
    lon: float
    radius_km: float


@dataclass
class AarProblem:
    origin: datetime
    tankers: list[Tanker]
    aarps: list[Aarp]
    demands: list[Demand]
    threats: list[Threat] = field(default_factory=list)
    pre_unserved: list[tuple[str, str, float, str]] = field(default_factory=list)
    time_limit_s: int = 15


def arc_blocked(problem: AarProblem, start: Point, end: Point) -> bool:
    return any(segment_hits_circle(start, end, (t.lat, t.lon), t.radius_km) for t in problem.threats)


def leg_min(tanker: Tanker, start: Point, end: Point) -> float:
    return travel_time_min(haversine_km(start[0], start[1], end[0], end[1]), tanker.speed_kts)


def evaluate_route(
    problem: AarProblem, tanker_idx: int, stops: list[tuple[int, int]]
) -> tuple[list[float], float] | None:
    """Earliest feasible service times (minutes) and total travel for (demand, aarp) stops, else None."""
    if not stops:
        return [], 0.0
    tanker = problem.tankers[tanker_idx]
    if sum(problem.demands[d].fuel_kg for d, _ in stops) > tanker.capacity_kg + 1e-6:
        return None
    here: Point = (tanker.lat, tanker.lon)
    points = [(problem.aarps[a].lat, problem.aarps[a].lon) for _, a in stops]
    travel = 0.0
    times: list[float] = []
    clock = 0.0
    depart = 0.0
    for order, (demand_idx, _) in enumerate(stops):
        demand = problem.demands[demand_idx]
        if arc_blocked(problem, here, points[order]):
            return None
        leg = leg_min(tanker, here, points[order])
        travel += leg
        if order == 0:
            depart = max(0.0, demand.start_min - leg)
            clock = depart
        else:
            clock += SERVICE_MIN
        clock = max(clock + leg, demand.start_min)
        if clock > demand.end_min + 1e-6:
            return None
        times.append(clock)
        here = points[order]
    home: Point = (tanker.lat, tanker.lon)
    if arc_blocked(problem, here, home):
        return None
    back = leg_min(tanker, here, home)
    travel += back
    if clock + SERVICE_MIN + back - depart > tanker.endurance_min + 1e-6:
        return None
    return times, travel


def classify_unserved(problem: AarProblem, demand_idx: int) -> str:
    """Why a demand could not be served, judged against each tanker flying it alone."""
    demand = problem.demands[demand_idx]
    if not problem.tankers:
        return "no_tanker"
    if all(demand.fuel_kg > t.capacity_kg for t in problem.tankers):
        return "capacity"
    for tanker_idx, tanker in enumerate(problem.tankers):
        if demand.fuel_kg > tanker.capacity_kg:
            continue
        if any(evaluate_route(problem, tanker_idx, [(demand_idx, a)]) for a in demand.aarp_idx):
            return "capacity"
    return "window"


def _centroid(text: str) -> Point:
    centre = shapely_wkt.loads(text).centroid
    return centre.y, centre.x


def build_problem(payload: ScheduleTankerAarInput, ctx: ToolContext) -> AarProblem:
    plan = ctx.plans.get(payload.plan_id)
    scenario = ctx.scenario.snapshot()
    bases = {base.base_id: base for base in scenario.bases}

    pool = [a for a in scenario.aircraft if TANKER_CAPABILITY in a.capabilities]
    if payload.tanker_tails is not None:
        by_tail = {a.tail_no: a for a in pool}
        unknown = [tail for tail in payload.tanker_tails if tail not in by_tail]
        if unknown:
            raise ToolInputError(f"not tanker aircraft: {unknown}; valid: {sorted(by_tail)}")
        pool = [by_tail[tail] for tail in payload.tanker_tails]
    tankers = [
        Tanker(
            tail=a.tail_no,
            lat=bases[a.base_id].lat,
            lon=bases[a.base_id].lon,
            capacity_kg=a.fuel_kg * RESERVE_FRACTION,
            endurance_min=a.range_km / (a.cruise_kts * 1.852) * 60.0 * RESERVE_FRACTION,
            speed_kts=a.cruise_kts,
        )
        for a in pool
        if a.status in TANKER_STATUSES
    ]

    tracks = [z for z in scenario.zones if z.type == "AAR_TRACK"]
    if payload.candidate_aarp_ids is not None:
        by_id = {z.zone_id: z for z in tracks}
        unknown = [zid for zid in payload.candidate_aarp_ids if zid not in by_id]
        if unknown:
            raise ToolInputError(f"unknown AARP ids: {unknown}; valid: {sorted(by_id)}")
        tracks = [by_id[zid] for zid in payload.candidate_aarp_ids]
    threats = [
        Threat(t.lat, t.lon, t.engagement_range_km) for t in scenario.threats if t.status == "ACTIVE"
    ]
    aarps = []
    for zone in sorted(tracks, key=lambda z: z.zone_id):
        lat, lon = _centroid(zone.wkt)
        if any(haversine_km(lat, lon, t.lat, t.lon) < t.radius_km for t in threats):
            continue
        aarps.append(Aarp(zone.zone_id, lat, lon))

    raw = []
    pre_unserved: list[tuple[str, str, float, str]] = []
    for leg in plan.legs:
        if not leg.needs_aar or leg.fuel_demand_kg <= 0:
            continue
        if not leg.waypoints:
            pre_unserved.append((leg.mission_id, leg.tail_no, leg.fuel_demand_kg, "no_route"))
            continue
        raw.append(leg)
    origin = min((leg.waypoints[0].eta for leg in raw), default=ctx.scenario_time)

    demands: list[Demand] = []
    for leg in raw:
        lat = sum(w.lat for w in leg.waypoints) / len(leg.waypoints)
        lon = sum(w.lon for w in leg.waypoints) / len(leg.waypoints)
        if not aarps:
            pre_unserved.append((leg.mission_id, leg.tail_no, leg.fuel_demand_kg, "no_aarp"))
            continue
        nearest = sorted(range(len(aarps)), key=lambda i: haversine_km(lat, lon, aarps[i].lat, aarps[i].lon))
        demands.append(
            Demand(
                mission_id=leg.mission_id,
                tail_no=leg.tail_no,
                fuel_kg=leg.fuel_demand_kg,
                start_min=(leg.waypoints[0].eta - origin).total_seconds() / 60.0,
                end_min=(leg.waypoints[-1].eta - origin).total_seconds() / 60.0,
                lat=lat,
                lon=lon,
                aarp_idx=tuple(nearest[:MAX_AARP_PER_DEMAND]),
            )
        )
    return AarProblem(origin, tankers, aarps, demands, threats, pre_unserved, payload.time_limit_s)
