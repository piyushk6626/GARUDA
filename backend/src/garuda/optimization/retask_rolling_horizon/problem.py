"""Gather candidate data through the frozen context and provider contracts.

The shared assignment backend is still a stub. Retasking owns its data, while
retaining AssignmentProblem's extension hook for frozen/stability constraints.
"""

import itertools
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import asin, cos, floor, isfinite, radians, sin, sqrt

from pydantic import ValidationError
from shapely import wkt

from garuda.common import as_utc
from garuda.contracts.geo import LatLon, TimeWindow
from garuda.contracts.plan import Assignment, PlanDraft
from garuda.contracts.tool import ToolInputError
from garuda.generators.crew import load_crew_catalog
from garuda.optimization.assign_missions.problem import AssignmentProblem
from garuda.queries.crew import availability_blockers
from garuda.schemas.mission import Mission
from garuda.schemas.threat import Threat


def assignment_key(item: Assignment) -> tuple:
    return (
        item.tail_no,
        item.config_id,
        item.mission_id,
        as_utc(item.depart),
        as_utc(item.tot),
    )


@dataclass
class Candidate:
    assignment: Assignment
    end: datetime
    base_id: str
    risk: float = 0.0
    pk: float = 0.0
    weapons: dict[str, int] = field(default_factory=dict)
    crew: dict[str, tuple[str, ...]] = field(default_factory=dict)
    staffing: dict[str, int] = field(default_factory=dict)
    needs_aar: bool = False
    fuel_demand: float = 0.0
    tanker_fuel: float = 0.0
    flight_hours: float = 0.0
    route_replan: bool = False
    fixed_crew: dict[str, tuple[str, ...]] = field(default_factory=dict)
    valid: bool = True
    reason: str = "NO_AIRCRAFT"

    @property
    def key(self) -> tuple[str, str, str, int]:
        a = self.assignment
        return (
            a.tail_no,
            a.config_id,
            a.mission_id,
            int(as_utc(a.depart).timestamp() * 1_000_000),
        )


@dataclass
class RetaskProblem(AssignmentProblem):
    plan: PlanDraft | None = None
    missions: dict[str, Mission] = field(default_factory=dict)
    candidates: list[Candidate] = field(default_factory=list)
    affected: set[str] = field(default_factory=set)
    frozen_missions: set[str] = field(default_factory=set)
    frozen_conflicts: set[str] = field(default_factory=set)
    historical: list[Assignment] = field(default_factory=list)
    stocks: dict[tuple[str, str], int] = field(default_factory=dict)
    capacities: dict[tuple[str, datetime], int] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    stability_weight: float = 5.0
    mode: str = "fast"
    time_limit_s: int = 15
    seed: int = 42
    route_replan: set[str] = field(default_factory=set)
    busy_until: dict[str, datetime] = field(default_factory=dict)
    inspection_hours: dict[str, float] = field(default_factory=dict)
    crew_duty: dict[str, float] = field(default_factory=dict)
    old_risk: dict[tuple, float] = field(default_factory=dict)


def _moment(value, name):
    try:
        moment = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        return as_utc(moment)
    except (ValueError, TypeError) as exc:
        raise ToolInputError(f"{name} must be an ISO datetime") from exc


def _probability(value, name):
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ToolInputError(f"{name} must be a number in [0, 1]") from exc
    if not isfinite(result) or not 0 <= result <= 1:
        raise ToolInputError(f"{name} must be a number in [0, 1]")
    return result


def _distance(a: LatLon, b: LatLon) -> float:
    lat1, lat2 = radians(a.lat), radians(b.lat)
    h = (
        sin((lat2 - lat1) / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(radians(b.lon - a.lon) / 2) ** 2
    )
    return 12742 * asin(sqrt(min(1.0, max(0.0, h))))


def _exposure(route, threats, at):
    """Conservative horizontal exposure; downstream routing resolves altitude."""
    survival = 1.0
    for threat in threats:
        if threat.status not in {"ACTIVE", "UNKNOWN"} or as_utc(threat.first_seen) > at:
            continue
        centre = LatLon(lat=threat.lat, lon=threat.lon)
        points = [
            (
                (p.lon - centre.lon) * 111.2 * cos(radians(centre.lat)),
                (p.lat - centre.lat) * 111.2,
            )
            for p in route
        ]
        distance = min((sqrt(x * x + y * y) for x, y in points), default=float("inf"))
        for (x, y), (u, v) in itertools.pairwise(points):
            dx, dy = u - x, v - y
            length = dx * dx + dy * dy
            ratio = min(1.0, max(0.0, -(x * dx + y * dy) / length)) if length else 0.0
            distance = min(
                distance, sqrt((x + ratio * dx) ** 2 + (y + ratio * dy) ** 2)
            )
        if distance <= threat.engagement_range_km:
            survival *= 1 - threat.pk_base * threat.confidence
    return 1 - survival


def build_problem(payload, ctx) -> RetaskProblem:
    if not isfinite(payload.freeze_hours) or payload.freeze_hours < 0:
        raise ToolInputError("freeze_hours must be finite and non-negative")
    if not isfinite(payload.stability_weight) or payload.stability_weight < 0:
        raise ToolInputError("stability_weight must be finite and non-negative")
    if payload.time_limit_s <= 0:
        raise ToolInputError("time_limit_s must be positive")
    try:
        original = ctx.plans.get(payload.plan_id)
    except KeyError as exc:
        raise ToolInputError(f"Unknown plan_id: {payload.plan_id}") from exc
    if original is None:
        raise ToolInputError(f"Unknown plan_id: {payload.plan_id}")
    plan = original.model_copy(deep=True)
    scenario = ctx.scenario.snapshot()
    missions = {m.mission_id: m.model_copy(deep=True) for m in scenario.missions}
    aircraft = {a.tail_no: a for a in scenario.aircraft}
    bases = {b.base_id: b for b in scenario.bases}
    configs = {c.config_id: c for c in scenario.load_configs}
    targets = {t.target_id: t for t in scenario.targets}
    now = as_utc(payload.event.time)
    params, entity, event_type = (
        payload.event.params,
        payload.event.entity_id,
        payload.event.type,
    )
    until = None
    for name in ("etr", "ETR", "maint_return_eta", "end_time", "until"):
        if name in params:
            until = _moment(params[name], name)
            break
    if until is not None and until <= now:
        raise ToolInputError("Event end/ETR must be after event.time")
    explicit = params.get("mission_ids", [])
    if not isinstance(explicit, list) or any(not isinstance(m, str) for m in explicit):
        raise ToolInputError("event.params.mission_ids must be a list of mission IDs")
    threats = list(scenario.threats)
    if event_type in {"aircraft_nmc", "tanker_lost"} and entity not in aircraft:
        raise ToolInputError(f"{event_type} requires a known aircraft entity_id")
    if event_type == "tanker_lost" and "AAR" not in aircraft[entity].capabilities:
        raise ToolInputError(
            "tanker_lost entity_id must identify an AAR-capable aircraft"
        )
    if event_type == "base_closed" and entity not in bases:
        raise ToolInputError("base_closed requires a known base entity_id")
    if event_type == "p1_request":
        if "mission" in params:
            try:
                requested = Mission.model_validate(params["mission"])
            except ValidationError as exc:
                raise ToolInputError(f"Invalid P1 mission: {exc}") from exc
            if entity is not None and entity != requested.mission_id:
                raise ToolInputError("P1 entity_id must match mission.mission_id")
            entity = requested.mission_id
            missions[entity] = requested
        if entity not in missions:
            raise ToolInputError(
                "p1_request needs a known mission entity_id or params.mission"
            )
        missions[entity] = missions[entity].model_copy(
            update={"priority": 1, "value": max(100.0, missions[entity].value)}
        )
    if event_type == "new_sam":
        if "threat" in params:
            try:
                threat = Threat.model_validate(params["threat"])
            except ValidationError as exc:
                raise ToolInputError(f"Invalid SAM threat: {exc}") from exc
            if entity is not None and entity != threat.threat_id:
                raise ToolInputError("SAM entity_id must match threat.threat_id")
            entity = threat.threat_id
            threats = [t for t in threats if t.threat_id != entity] + [threat]
        elif entity not in {t.threat_id for t in threats}:
            raise ToolInputError(
                "new_sam needs a known threat entity_id or params.threat"
            )
    weather_override = None
    if event_type == "weather_override":
        weather_override = _probability(
            params.get("p_go", params.get("flyability")), "weather p_go/flyability"
        )
        if entity not in bases and entity not in missions and not explicit:
            raise ToolInputError(
                "weather_override needs a base/mission entity_id or mission_ids"
            )
    unknown = set(explicit) - missions.keys()
    if unknown:
        raise ToolInputError(f"Unknown affected missions: {sorted(unknown)}")

    problem = RetaskProblem(
        plan=plan,
        missions=missions,
        affected=set(explicit),
        stability_weight=payload.stability_weight,
        mode=payload.mode,
        time_limit_s=payload.time_limit_s,
        seed=ctx.seed,
    )
    problem.inspection_hours = {
        a.tail_no: a.hours_to_next_inspection for a in scenario.aircraft
    }
    problem.crew_duty = {c.crew_id: c.duty_hours_24h for c in scenario.crew}
    stock_counts = Counter()
    for stock in scenario.stocks:
        stock_counts[stock.base_id, stock.weapon_code] += (
            stock.quantity - stock.reserved_qty
        )
    problem.stocks = dict(stock_counts)
    legs = {(leg.mission_id, leg.tail_no): leg for leg in plan.legs}
    slots = {(slot.mission_id, slot.tail_no): slot for slot in plan.sortie_slots}
    old_by_mission, keys = {}, set()
    for assignment in plan.assignments:
        key = assignment_key(assignment)
        if key in keys:
            raise ToolInputError("Plan contains duplicate assignments")
        keys.add(key)
        if assignment.mission_id not in missions or assignment.tail_no not in aircraft:
            raise ToolInputError(
                "Plan assignment references an unknown mission or aircraft"
            )
        if as_utc(assignment.tot) < as_utc(assignment.depart):
            raise ToolInputError("Plan assignment TOT precedes departure")
        mission = missions[assignment.mission_id]
        end = (
            as_utc(assignment.tot)
            + timedelta(minutes=mission.duration_min)
            + (as_utc(assignment.tot) - as_utc(assignment.depart))
        )
        slot = slots.get((assignment.mission_id, assignment.tail_no))
        if slot:
            end = max(end, as_utc(slot.end))
        if end <= now or mission.status in {"DONE", "CANCELLED"}:
            problem.historical.append(assignment)
            ready_at = end + timedelta(
                minutes=aircraft[assignment.tail_no].turnaround_min
            )
            problem.busy_until[assignment.tail_no] = max(
                ready_at, problem.busy_until.get(assignment.tail_no, ready_at)
            )
            continue
        old_by_mission.setdefault(assignment.mission_id, []).append(assignment)
        if (
            as_utc(assignment.depart) <= now + timedelta(hours=payload.freeze_hours)
            or mission.status == "EXECUTING"
        ):
            problem.frozen_missions.add(assignment.mission_id)
        if (
            event_type in {"aircraft_nmc", "tanker_lost"}
            and assignment.tail_no == entity
        ):
            problem.affected.add(assignment.mission_id)
        if (
            event_type == "base_closed"
            and aircraft[assignment.tail_no].base_id == entity
        ):
            problem.affected.add(assignment.mission_id)
    problem.locked = [
        a
        for a in plan.assignments
        if a.mission_id in problem.frozen_missions and a not in problem.historical
    ]
    if event_type == "p1_request":
        problem.affected.add(entity)
    compatibility = {
        (c.aircraft_type, c.weapon_code): set(c.allowed_stations)
        for c in scenario.compatibilities
    }
    weapons = {w.weapon_code: w for w in scenario.weapons}
    staffing_catalog = load_crew_catalog()
    active_ids = (
        set(old_by_mission) | {u.mission_id for u in plan.unassigned} | problem.affected
    )
    active_ids |= {
        m.mission_id
        for m in missions.values()
        if m.status not in {"DONE", "CANCELLED"}
        and as_utc(m.window_end) > now
        and as_utc(m.window_start) < as_utc(plan.scenario_time) + timedelta(hours=72)
    }

    def destination(mission, origin):
        if mission.target_id:
            target = targets.get(mission.target_id)
            if target is None:
                raise ToolInputError(f"Unknown target {mission.target_id}")
            return LatLon(lat=target.lat, lon=target.lon)
        if mission.area_wkt:
            try:
                centre = wkt.loads(mission.area_wkt).centroid
                return LatLon(lat=centre.y, lon=centre.x)
            except (ValueError, ValidationError) as exc:
                raise ToolInputError(
                    f"Invalid area_wkt for {mission.mission_id}"
                ) from exc
        return origin

    def candidate(mission, airframe, config, assignment, original=False):
        depart, tot = as_utc(assignment.depart), as_utc(assignment.tot)
        airborne = original and depart <= now
        base = bases.get(airframe.base_id)
        if base is None:
            raise ToolInputError(f"Unknown launch base {airframe.base_id}")
        origin = LatLon(lat=base.lat, lon=base.lon)
        dest = destination(mission, origin)
        route = ctx.providers.route.route(
            origin, dest, airframe.type, assignment.depart
        )
        route = [LatLon.model_validate(p) for p in route] or [origin, dest]
        leg = legs.get((mission.mission_id, airframe.tail_no)) if original else None
        if leg and leg.waypoints:
            route = [LatLon(lat=p.lat, lon=p.lon) for p in leg.waypoints]
        distance = sum(_distance(a, b) for a, b in itertools.pairwise(route))
        travel = timedelta(hours=distance / (airframe.cruise_kts * 1.852))
        recovery = (
            tot + timedelta(minutes=mission.duration_min) + max(travel, tot - depart)
        )
        slot = slots.get((mission.mission_id, airframe.tail_no)) if original else None
        if slot:
            recovery = max(recovery, as_utc(slot.end))
        item = Candidate(
            assignment=assignment,
            end=recovery + timedelta(minutes=airframe.turnaround_min),
            base_id=base.base_id,
        )
        item.flight_hours = (recovery - depart).total_seconds() / 3600
        blockers = []
        if not set(mission.required_capabilities).issubset(airframe.capabilities):
            blockers.append("NO_AIRCRAFT")
        if not airborne:
            if (
                depart < max(now, problem.busy_until.get(airframe.tail_no, now))
                or airframe.fuel_kg < airframe.fuel_capacity_kg * 0.2
            ):
                blockers.append("NO_AIRCRAFT")
            ready = airframe.status == "FMC" or (
                airframe.maint_return_eta is not None
                and as_utc(airframe.maint_return_eta) <= depart
            )
            if airframe.status == "IN_MISSION":
                ready = any(
                    a.tail_no == airframe.tail_no
                    for a in problem.locked + problem.historical
                )
            if (
                not ready
                or _probability(
                    ctx.providers.availability.p_fmc(airframe.tail_no, depart),
                    "availability.p_fmc",
                )
                < 0.5
            ):
                blockers.append("NO_AIRCRAFT")
        if (
            recovery - depart
        ).total_seconds() / 3600 > airframe.hours_to_next_inspection:
            blockers.append("NO_AIRCRAFT")
        if (
            event_type in {"aircraft_nmc", "tanker_lost"}
            and airframe.tail_no == entity
            and recovery > now
            and (event_type == "tanker_lost" or until is None or depart < until)
        ):
            blockers.append(
                "NO_TANKER" if event_type == "tanker_lost" else "NO_AIRCRAFT"
            )
        if (
            event_type == "base_closed"
            and base.base_id == entity
            and not airborne
            and (until is None or depart < until)
        ):
            blockers.append("BASE_CAPACITY")
        if (
            not (as_utc(mission.window_start) <= tot <= as_utc(mission.window_end))
            or tot - depart < travel
        ):
            blockers.append("WINDOW")
        if config:
            if config.aircraft_type != airframe.type or config.role != {
                "CAP": "DCA"
            }.get(mission.type, mission.type):
                blockers.append("NO_AIRCRAFT")
            for station, loads in config.stations.items():
                for load in loads:
                    code, count = load.rsplit("x", 1)
                    item.weapons[code] = item.weapons.get(code, 0) + int(count)
                    if (
                        station not in compatibility.get((airframe.type, code), set())
                        or code not in weapons
                    ):
                        blockers.append("NO_WEAPON_STOCK")
            effective_range = airframe.range_km * (1 - config.range_penalty_pct / 100)
        else:
            effective_range = airframe.range_km
            if mission.type not in {"AAR", "AIRLIFT", "SAR"}:
                blockers.append("NO_AIRCRAFT")
        mission_legs = [l for l in plan.legs if l.mission_id == mission.mission_id]
        mission_slots = [
            s for s in plan.sortie_slots if s.mission_id == mission.mission_id
        ]
        item.needs_aar = bool(
            any(l.needs_aar for l in mission_legs)
            or any(s.needs_aar for s in mission_slots)
            or 2 * distance > effective_range
        )
        item.fuel_demand = max((l.fuel_demand_kg for l in mission_legs), default=0.0)
        if item.needs_aar:
            item.fuel_demand = max(
                item.fuel_demand,
                airframe.fuel_capacity_kg
                * max(0.2, 2 * distance / effective_range - 1),
            )
        item.tanker_fuel = (
            max(0.0, airframe.fuel_kg - airframe.fuel_capacity_kg * 0.2)
            if mission.type == "AAR"
            else 0.0
        )
        item.pk = _probability(
            ctx.providers.pk.pk(assignment.config_id, mission.mission_id), "pk.pk"
        )
        if item.pk < mission.min_pk:
            blockers.append("NO_WEAPON_STOCK")
        window = TimeWindow(start=assignment.depart, end=recovery)
        guidance = next(
            (weapons[c].guidance for c in item.weapons if c in weapons), "NONE"
        )
        go = _probability(
            ctx.providers.weather_go.p_go(base.base_id, route, window, guidance),
            "weather_go.p_go",
        )
        weather_hit = event_type == "weather_override" and (
            entity in {base.base_id, mission.mission_id}
            or mission.mission_id in explicit
        )
        if weather_hit and (until is None or depart < until):
            go = weather_override
            problem.affected.add(mission.mission_id)
        if go < 0.5:
            blockers.append("WEATHER")
        risk = _exposure(route, threats, max(now, tot))
        component_risk = _probability(
            ctx.providers.component_risk.aircraft_risk(
                airframe.tail_no, (recovery - depart).total_seconds() / 3600
            ),
            "component risk",
        )
        item.risk = 1 - (1 - risk) * (1 - component_risk)
        if original:
            prior_threats = [
                t
                for t in scenario.threats
                if event_type != "new_sam" or t.threat_id != entity
            ]
            prior_risk = _exposure(route, prior_threats, max(now, tot))
            problem.old_risk[assignment_key(assignment)] = 1 - (1 - prior_risk) * (
                1 - component_risk
            )
        if (
            event_type == "new_sam"
            and _exposure(
                route, [t for t in threats if t.threat_id == entity], max(tot, now)
            )
            > 0
        ):
            problem.affected.add(mission.mission_id)
            item.route_replan = True
            if original:
                problem.route_replan.add(mission.mission_id)
        if event_type == "tanker_lost" and item.needs_aar:
            problem.affected.add(mission.mission_id)
        item.staffing = dict(staffing_catalog.types[airframe.type].staffing)
        qualification = {
            "DCA": {"BVR"},
            "CAP": {"BVR"},
            "STRIKE": {"AG", "STRIKE"},
            "CAS": {"AG", "CAS"},
            "SEAD": {"AG", "SEAD"},
            "ISR": {"ISR", "AEW"},
        }.get(mission.type, {mission.type})
        for role in item.staffing:
            eligible = []
            fixed = []
            for member in scenario.crew:
                if member.role != role or member.base_id != base.base_id:
                    continue
                bound = (
                    original
                    and slot
                    and any(
                        c.crew_id == member.crew_id and c.slot_id == slot.slot_id
                        for c in plan.crew_assignments
                    )
                )
                if bound and mission.mission_id in problem.frozen_missions:
                    fixed.append(member.crew_id)
                if not bound and availability_blockers(
                    member,
                    at=depart,
                    aircraft_type=airframe.type,
                    need_aar=item.needs_aar,
                ):
                    continue
                if (
                    not bound
                    and role in {"PILOT", "WSO"}
                    and not qualification.intersection(
                        member.qualifications.get(airframe.type, [])
                    )
                ):
                    continue
                if (
                    not bound
                    and member.duty_hours_24h
                    + (recovery - depart).total_seconds() / 3600
                    > 14
                ):
                    continue
                eligible.append(member.crew_id)
            item.crew[role] = tuple(eligible)
            item.fixed_crew[role] = tuple(fixed)
            if len(eligible) < item.staffing[role] and not airborne:
                blockers.append("NO_QUALIFIED_CREW")
        if airborne:
            item.staffing = {
                role: len(ids) for role, ids in item.fixed_crew.items() if ids
            }
        item.valid = not blockers
        item.reason = blockers[0] if blockers else "NO_AIRCRAFT"
        hour = depart.replace(minute=0, second=0, microsecond=0)
        cap_key = base.base_id, hour
        if cap_key not in problem.capacities:
            cap = ctx.providers.capacity.cap(base.base_id, hour)
            if not isfinite(cap) or cap < 0:
                raise ToolInputError("capacity.cap must be finite and non-negative")
            problem.capacities[cap_key] = min(
                floor(cap), floor(base.max_sorties_per_hour)
            )
        return item

    candidate_keys = set()
    for mission_id in sorted(active_ids):
        mission = missions.get(mission_id)
        if mission is None:
            raise ToolInputError(f"Unknown mission {mission_id} in plan")
        if mission.status in {"DONE", "CANCELLED"} or (
            as_utc(mission.window_end) <= now and mission_id not in old_by_mission
        ):
            continue
        old = old_by_mission.get(mission_id, [])
        for assignment in old:
            item = candidate(
                mission,
                aircraft[assignment.tail_no],
                configs.get(assignment.config_id),
                assignment,
                True,
            )
            problem.candidates.append(item)
            candidate_keys.add(item.key)
            if not item.valid:
                problem.affected.add(mission_id)
                problem.reasons[mission_id] = item.reason
                if mission_id in problem.frozen_missions:
                    problem.frozen_conflicts.add(mission_id)
        if mission_id in problem.frozen_missions:
            continue
        tots = {as_utc(a.tot) for a in old}
        first = max(now, as_utc(mission.window_start))
        while first <= as_utc(mission.window_end):
            tots.add(first)
            first += timedelta(hours=1)
        tots.add(as_utc(mission.window_end))
        reasons = Counter()
        for airframe in sorted(aircraft.values(), key=lambda a: a.tail_no):
            if not set(mission.required_capabilities).issubset(airframe.capabilities):
                continue
            options = [
                (c.config_id, c)
                for c in scenario.load_configs
                if c.aircraft_type == airframe.type
                and c.role == {"CAP": "DCA"}.get(mission.type, mission.type)
            ]
            if mission.type in {"AAR", "AIRLIFT", "SAR"}:
                options += [
                    (a.config_id, None)
                    for a in old
                    if a.config_id not in configs
                    and aircraft[a.tail_no].type == airframe.type
                ]
            base = bases.get(airframe.base_id)
            if base is None:
                continue
            origin = LatLon(lat=base.lat, lon=base.lon)
            dest = destination(mission, origin)
            for tot in sorted(tots):
                straight_depart = tot - timedelta(
                    hours=_distance(origin, dest) / (airframe.cruise_kts * 1.852)
                )
                if straight_depart < now:
                    continue
                route = ctx.providers.route.route(
                    origin, dest, airframe.type, straight_depart
                )
                route = [LatLon.model_validate(p) for p in route] or [origin, dest]
                depart = tot - timedelta(
                    hours=sum(_distance(a, b) for a, b in itertools.pairwise(route))
                    / (airframe.cruise_kts * 1.852)
                )
                for config_id, config in options:
                    assignment = Assignment(
                        tail_no=airframe.tail_no,
                        config_id=config_id,
                        mission_id=mission_id,
                        depart=depart,
                        tot=tot,
                    )
                    item = candidate(mission, airframe, config, assignment)
                    if item.key in candidate_keys:
                        continue
                    reasons[item.reason] += 1
                    if item.valid:
                        problem.candidates.append(item)
                        candidate_keys.add(item.key)
        if not any(
            c.valid and c.assignment.mission_id == mission_id
            for c in problem.candidates
        ):
            problem.reasons.setdefault(
                mission_id, reasons.most_common(1)[0][0] if reasons else "NO_AIRCRAFT"
            )
    return problem
