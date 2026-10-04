"""Private scenario preparation; the frozen AssignmentProblem interface is unchanged.

One departure candidate is sampled in each six-hour bucket (including short
mission windows). Locks retain their exact times. Providers supply forecasts;
only dispatch probability >= .5 and weather GO probability >= .5 are accepted.
Range is checked without assuming unmodelled tanker fuel or offload capacity.
"""

import itertools
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import asin, cos, isfinite, radians, sin, sqrt

from shapely import wkt

from garuda.contracts.geo import LatLon, TimeWindow
from garuda.contracts.plan import Assignment
from garuda.contracts.tool import ToolInputError
from garuda.optimization.assign_missions.problem import AssignmentProblem
from garuda.queries.crew import availability_blockers
from garuda.schemas.mission import Mission

BUCKET_HOURS = 6
# These are seats per sortie, not the number of roster members per airframe.
SEATS = {
    "Su-30MKI": ("PILOT", "WSO"),
    "C-130J": ("PILOT", "LOADMASTER"),
    "AEW&C": ("PILOT", "AEW_OP"),
    "IL-78": ("PILOT", "PILOT"),
}
ROLE = {"CAP": "DCA"}
QUALIFICATION = {
    "DCA": "BVR",
    "CAP": "BVR",
    "STRIKE": "AG",
    "SEAD": "AG",
    "CAS": "AG",
    "ISR": "ISR",
    "AAR": "AAR",
    "AIRLIFT": "AIRLIFT",
    "SAR": "SAR",
}


@dataclass(frozen=True)
class Candidate:
    key: tuple[str, str, str, int]
    assignment: Assignment
    base: str
    bucket: int
    recover: datetime
    ready: datetime
    hours: float
    resources: dict[str, int]
    pk: float
    risk: float
    # Each role maps to the eligible crew IDs and the number of seats.
    crew: dict[str, tuple[tuple[str, ...], int]]
    night: bool = False
    aircraft_type: str = ""


@dataclass
class PreparedProblem(AssignmentProblem):
    missions: dict[str, Mission] = field(default_factory=dict)
    candidates: dict[tuple, Candidate] = field(default_factory=dict)
    stock: dict[tuple[str, str], int] = field(default_factory=dict)
    capacity: dict[tuple[str, int], int] = field(default_factory=dict)
    inspection: dict[str, float] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)
    rejected: dict[str, Counter] = field(default_factory=dict)
    lock_keys: set[tuple] = field(default_factory=set)
    lock_errors: list[str] = field(default_factory=list)
    risk_weight: float = 0.2
    effect_weight: float = 0.3
    time_limit_s: int = 30
    seed: int = 42


def distance_km(a: LatLon, b: LatLon) -> float:
    dlat, dlon = radians(b.lat - a.lat), radians(b.lon - a.lon)
    h = (
        sin(dlat / 2) ** 2
        + cos(radians(a.lat)) * cos(radians(b.lat)) * sin(dlon / 2) ** 2
    )
    return 6371.0088 * 2 * asin(sqrt(min(1.0, h)))


def probability(value, name: str) -> float:
    value = float(value)
    if not isfinite(value) or not 0 <= value <= 1:
        raise ToolInputError(f"{name} must return a finite probability in [0, 1]")
    return value


def indexed(rows, name: str):
    result = {}
    for row in rows:
        key = getattr(row, name)
        if key in result:
            raise ToolInputError(f"Duplicate {name}: {key}")
        result[key] = row
    return result


def build_problem(payload, ctx) -> PreparedProblem:
    """Read one snapshot and build sparse, feasible assignment tuples."""
    if payload.scenario_time.utcoffset() is None:
        raise ToolInputError("scenario_time must be timezone-aware")
    if payload.horizon_hours <= 0 or payload.time_limit_s <= 0:
        raise ToolInputError("horizon_hours and time_limit_s must be positive")
    for name in ("risk", "effect"):
        value = getattr(payload.weights, name)
        if not isfinite(value) or value < 0:
            raise ToolInputError(f"weights.{name} must be finite and non-negative")
    scenario = ctx.scenario.snapshot()
    all_missions = indexed(scenario.missions, "mission_id")
    aircraft = indexed(scenario.aircraft, "tail_no")
    configs = indexed(scenario.load_configs, "config_id")
    bases = indexed(scenario.bases, "base_id")
    targets = indexed(scenario.targets, "target_id")
    weapons = indexed(scenario.weapons, "weapon_code")
    indexed(scenario.crew, "crew_id")
    if payload.mission_ids is not None:
        unknown = set(payload.mission_ids) - all_missions.keys()
        if unknown:
            raise ToolInputError(f"Unknown mission_ids: {', '.join(sorted(unknown))}")
    requested = (
        set(all_missions) if payload.mission_ids is None else set(payload.mission_ids)
    )
    missions = {
        mid: m
        for mid, m in sorted(all_missions.items())
        if mid in requested and m.status in {"REQUESTED", "PLANNED"}
    }
    p = PreparedProblem(
        locked=[a.model_copy(deep=True) for a in payload.locked_assignments],
        missions=missions,
        completed={mid for mid, m in all_missions.items() if m.status == "DONE"},
        risk_weight=payload.weights.risk,
        effect_weight=payload.weights.effect,
        time_limit_s=payload.time_limit_s,
        seed=ctx.seed,
    )
    p.rejected = {mid: Counter() for mid in missions}
    end = payload.scenario_time + timedelta(hours=payload.horizon_hours)
    compatibility = {
        (c.aircraft_type, c.weapon_code): set(c.allowed_stations)
        for c in scenario.compatibilities
    }
    resources = {}
    for cid, config in configs.items():
        loads = Counter()
        valid = True
        for station, tokens in config.stations.items():
            for token in tokens:
                code, count = token.rsplit("x", 1)
                loads[code] += int(count)
                valid &= code in weapons and station in compatibility.get(
                    (config.aircraft_type, code), set()
                )
        if valid:
            resources[cid] = dict(loads)
    for row in scenario.stocks:
        key = row.base_id, row.weapon_code
        if key in p.stock:
            raise ToolInputError(f"Duplicate resource stock: {key}")
        p.stock[key] = row.quantity - row.reserved_qty
    locks = {}
    for lock in p.locked:
        if (
            lock.mission_id not in missions
            or lock.tail_no not in aircraft
            or lock.config_id not in configs
        ):
            raise ToolInputError(
                "Locked assignments must reference selected pending missions, aircraft and configs"
            )
        if lock.depart.utcoffset() is None or lock.tot.utcoffset() is None:
            raise ToolInputError("Locked assignment times must be timezone-aware")
        bucket = int(
            (lock.depart - payload.scenario_time).total_seconds()
            // (BUCKET_HOURS * 3600)
        )
        key = lock.tail_no, lock.config_id, lock.mission_id, bucket
        if key in locks and locks[key] != lock:
            p.lock_errors.append(f"Conflicting exact locks for {key}")
        locks[key] = lock
    for mid, mission in missions.items():
        if (
            mission.window_start.utcoffset() is None
            or mission.window_end.utcoffset() is None
        ):
            raise ToolInputError(f"Mission {mid} has timezone-naive window times")
        if mission.min_aircraft > mission.max_aircraft:
            raise ToolInputError(f"Mission {mid}: min_aircraft exceeds max_aircraft")
        if not isfinite(mission.value):
            raise ToolInputError(f"Mission {mid}: value must be finite")
        if not any(
            c.role == ROLE.get(mission.type, mission.type) for c in configs.values()
        ):
            p.rejected[mid][
                (
                    "NO_AIRCRAFT",
                    f"No matching load configuration for mission role {mission.type}",
                )
            ] += 1
            continue
        if mission.window_end <= payload.scenario_time or mission.window_start >= end:
            p.rejected[mid][
                ("WINDOW", "Mission window does not overlap the planning horizon")
            ] += 1
            continue
        if mission.target_id is not None:
            if mission.target_id not in targets:
                raise ToolInputError(
                    f"Mission {mid}: unknown target {mission.target_id}"
                )
            target = targets[mission.target_id]
            destination = LatLon(lat=target.lat, lon=target.lon)
        elif mission.area_wkt:
            try:
                area = wkt.loads(mission.area_wkt)
                if area.is_empty or not area.is_valid:
                    raise ValueError("invalid mission area")
                point = area.representative_point()
                destination = LatLon(lat=point.y, lon=point.x)
            except (ValueError, TypeError) as exc:
                raise ToolInputError(f"Mission {mid}: invalid area_wkt") from exc
        else:
            p.rejected[mid][("WINDOW", "Mission has no target or operating area")] += 1
            continue
        for tail, airframe in sorted(aircraft.items()):
            if airframe.base_id not in bases:
                raise ToolInputError(
                    f"Aircraft {tail}: unknown base {airframe.base_id}"
                )
            p.inspection[tail] = airframe.hours_to_next_inspection
            if not set(mission.required_capabilities) <= set(airframe.capabilities):
                p.rejected[mid][
                    ("NO_AIRCRAFT", "Required aircraft capabilities unavailable")
                ] += 1
                continue
            base = bases[airframe.base_id]
            origin = LatLon(lat=base.lat, lon=base.lon)
            for cid, config in sorted(configs.items()):
                if config.aircraft_type != airframe.type or config.role != ROLE.get(
                    mission.type, mission.type
                ):
                    continue
                if cid not in resources:
                    p.rejected[mid][
                        ("NO_AIRCRAFT", "Configuration has incompatible station loads")
                    ] += 1
                    continue
                loads = resources[cid]
                pk = probability(ctx.providers.pk.pk(cid, mid), "pk")
                if pk < mission.min_pk:
                    p.rejected[mid][
                        (
                            "NO_AIRCRAFT",
                            "Configuration effectiveness below mission min_pk",
                        )
                    ] += 1
                    continue
                for bucket in range(
                    (payload.horizon_hours + BUCKET_HOURS - 1) // BUCKET_HOURS
                ):
                    start = payload.scenario_time + timedelta(
                        hours=bucket * BUCKET_HOURS
                    )
                    stop = min(end, start + timedelta(hours=BUCKET_HOURS))
                    if start >= mission.window_end:
                        continue
                    key = tail, cid, mid, bucket
                    lock = locks.get(key)
                    # Route at the bucket start; reroute at the actual departure below.
                    route = ctx.providers.route.route(
                        origin, destination, airframe.type, start
                    )
                    if not route:
                        p.rejected[mid][
                            ("NO_AIRCRAFT", "Route provider found no feasible route")
                        ] += 1
                        continue
                    route = [origin, *route, destination]
                    km = sum(distance_km(a, b) for a, b in itertools.pairwise(route))
                    travel = timedelta(hours=km / (airframe.cruise_kts * 1.852))
                    depart = (
                        lock.depart
                        if lock
                        else max(start, mission.window_start - travel)
                    )
                    if not lock and airframe.maint_return_eta is not None:
                        depart = max(depart, airframe.maint_return_eta)
                    if not start <= depart < stop:
                        continue
                    if depart != start:
                        for _ in range(5):
                            route = ctx.providers.route.route(
                                origin, destination, airframe.type, depart
                            )
                            if not route:
                                break
                            route = [origin, *route, destination]
                            km = sum(
                                distance_km(a, b) for a, b in itertools.pairwise(route)
                            )
                            travel = timedelta(hours=km / (airframe.cruise_kts * 1.852))
                            adjusted = (
                                depart
                                if lock
                                else max(depart, mission.window_start - travel)
                            )
                            if adjusted == depart:
                                break
                            depart = adjusted
                        else:
                            p.rejected[mid][
                                (
                                    "WINDOW",
                                    "Time-dependent route could not stabilize departure",
                                )
                            ] += 1
                            continue
                        if not route:
                            p.rejected[mid][
                                (
                                    "NO_AIRCRAFT",
                                    "Route provider found no feasible route",
                                )
                            ] += 1
                            continue
                    tot = lock.tot if lock else depart + travel
                    finish = tot + timedelta(minutes=mission.duration_min)
                    recover = finish + travel
                    hours = (recover - depart).total_seconds() / 3600
                    reason = None
                    if (
                        not start <= depart < stop
                        or tot < depart + travel
                        or tot < mission.window_start
                        or finish > mission.window_end
                        or recover > end
                    ):
                        reason = (
                            "WINDOW",
                            "Travel, task duration or recovery falls outside the window/horizon",
                        )
                    elif 2 * km > airframe.range_km * (
                        1 - config.range_penalty_pct / 100
                    ):
                        reason = (
                            "NO_TANKER",
                            "Round-trip distance exceeds configured range; no certified refueling capacity",
                        )
                    elif (
                        airframe.status == "IN_MISSION"
                        or (
                            airframe.status != "FMC"
                            and (
                                airframe.maint_return_eta is None
                                or depart < airframe.maint_return_eta
                            )
                        )
                        or airframe.fuel_kg < 0.2 * airframe.fuel_capacity_kg
                        or hours > airframe.hours_to_next_inspection
                        or probability(
                            ctx.providers.availability.p_fmc(tail, depart),
                            "availability",
                        )
                        < 0.5
                    ):
                        reason = (
                            "NO_AIRCRAFT",
                            "Aircraft unavailable or insufficient fuel/inspection margin",
                        )
                    elif any(
                        p.stock.get((base.base_id, code), 0) < count
                        for code, count in loads.items()
                    ):
                        reason = (
                            "NO_WEAPON_STOCK",
                            "Insufficient usable configuration stock at launch base",
                        )
                    if reason:
                        p.rejected[mid][reason] += 1
                        continue
                    guidance = {weapons[code].guidance for code in loads}
                    window = TimeWindow(start=depart, end=recover)
                    if any(
                        probability(
                            ctx.providers.weather_go.p_go(
                                base.base_id, route, window, g
                            ),
                            "weather_go",
                        )
                        < 0.5
                        for g in sorted(guidance)
                    ):
                        p.rejected[mid][
                            ("WEATHER", "Weather GO probability below 0.5")
                        ] += 1
                        continue
                    cap_key = base.base_id, bucket
                    if cap_key not in p.capacity:
                        # cap(hour) is hourly; integrate every hour in the six-hour bucket.
                        capacity = 0.0
                        hour = start
                        while hour < stop:
                            cap = ctx.providers.capacity.cap(base.base_id, hour)
                            if not isfinite(cap) or cap < 0:
                                raise ToolInputError(
                                    "capacity must be finite and non-negative"
                                )
                            span = min(1.0, (stop - hour).total_seconds() / 3600)
                            capacity += min(cap, base.max_sorties_per_hour) * span
                            hour += timedelta(hours=1)
                        p.capacity[cap_key] = int(capacity)
                    if not p.capacity[cap_key]:
                        p.rejected[mid][
                            (
                                "BASE_CAPACITY",
                                "No base departure capacity in this bucket",
                            )
                        ] += 1
                        continue
                    crew = {}
                    # Solar-time approximation for currency; the shared crew query uses 90-day currency.
                    local_hour = (depart.hour + depart.minute / 60 + base.lon / 15) % 24
                    night = (
                        local_hour < 6 or local_hour >= 18 or hours >= 18 - local_hour
                    )
                    for role, count in Counter(
                        SEATS.get(airframe.type, ("PILOT",))
                    ).items():
                        eligible = []
                        for member in scenario.crew:
                            quals = member.qualifications.get(airframe.type, [])
                            qualified = (
                                mission.type in quals
                                or QUALIFICATION[mission.type] in quals
                            )
                            if (
                                member.base_id == base.base_id
                                and member.role == role
                                and qualified
                                and (role != "WSO" or member.sqn_id == airframe.sqn_id)
                                and hours <= 14
                                and not availability_blockers(
                                    member,
                                    at=depart,
                                    aircraft_type=airframe.type,
                                    need_night=night,
                                    need_aar=mission.type == "AAR",
                                )
                                and probability(
                                    ctx.providers.fatigue.fatigue(
                                        member.crew_id, depart
                                    ),
                                    "fatigue",
                                )
                                <= 0.7
                            ):
                                eligible.append(member.crew_id)
                        crew[role] = tuple(sorted(eligible)), count
                    if any(len(ids) < count for ids, count in crew.values()):
                        p.rejected[mid][
                            (
                                "NO_QUALIFIED_CREW",
                                "Insufficient qualified, current and rested crew",
                            )
                        ] += 1
                        continue
                    risk = probability(
                        ctx.providers.component_risk.aircraft_risk(tail, hours),
                        "component_risk",
                    )
                    p.candidates[key] = Candidate(
                        key,
                        Assignment(
                            tail_no=tail,
                            config_id=cid,
                            mission_id=mid,
                            depart=depart,
                            tot=tot,
                        ),
                        base.base_id,
                        bucket,
                        recover,
                        recover + timedelta(minutes=airframe.turnaround_min),
                        hours,
                        loads,
                        pk,
                        risk,
                        crew,
                        night,
                        airframe.type,
                    )
        if (
            not any(c.assignment.mission_id == mid for c in p.candidates.values())
            and not p.rejected[mid]
        ):
            p.rejected[mid][
                (
                    "NO_AIRCRAFT",
                    f"No matching load configuration for mission role {mission.type}",
                )
            ] += 1
    for key, lock in locks.items():
        if key not in p.candidates or p.candidates[key].assignment != lock:
            p.lock_errors.append(
                f"Locked assignment is not feasible: {lock.tail_no}/{lock.mission_id} at {lock.depart.isoformat()}"
            )
        else:
            p.lock_keys.add(key)
    return p
