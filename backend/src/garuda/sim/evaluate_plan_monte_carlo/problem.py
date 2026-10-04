"""Read frozen plan/scenario/provider contracts into a simulator-only snapshot."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import math

from garuda.contracts.geo import LatLon, TimeWindow, Waypoint
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.schemas.mission import Mission
from garuda.schemas.threat import Threat
from garuda.sim.evaluate_plan_monte_carlo.schemas import EvaluatePlanMonteCarloInput


@dataclass(frozen=True)
class AircraftOption:
    tail_no: str
    p_launch: float
    p_failure: float
    turnaround_min: float


@dataclass(frozen=True)
class Sortie:
    mission_id: str
    depart: float
    tot: float
    end: float
    day: str
    aircraft: tuple[AircraftOption, ...]
    p_go: float
    p_effect: float
    exposures: tuple[tuple[str, float], ...]
    needs_aar: bool


@dataclass(frozen=True)
class Problem:
    missions: tuple[Mission, ...]
    sorties: tuple[Sortie, ...]
    threats: tuple[Threat, ...]
    threshold: float
    n_runs: int
    seed: int
    stress: str


def _probability(value: float, label: str) -> float:
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ToolInputError(f"{label} must be a finite probability in [0, 1]")
    return float(value)


def _time(moment: datetime) -> float:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ToolInputError("Simulation timestamps must be timezone-aware")
    return moment.timestamp() / 60.0


def exposure_hours(waypoints: list[Waypoint], threat: Threat) -> float:
    """Clip each linear segment to the horizontal and altitude threat envelope.

    Distances use a local tangent-plane approximation for theatre-scale routes.
    Exposure is in hours; the simulator clips Pk * confidence * exposure to [0, 1].
    """
    if threat.status == "DESTROYED" or threat.engagement_range_km == 0:
        return 0.0
    total = 0.0
    lon_scale = 111.195 * math.cos(math.radians(threat.lat))
    for left, right in zip(waypoints, waypoints[1:]):
        minutes = _time(right.eta) - _time(left.eta)
        if minutes < 0:
            raise ToolInputError("Flight-leg waypoint ETAs must be nondecreasing")
        x = ((left.lon - threat.lon + 180) % 360 - 180) * lon_scale
        y = (left.lat - threat.lat) * 111.195
        dx = ((right.lon - left.lon + 180) % 360 - 180) * lon_scale
        dy = (right.lat - left.lat) * 111.195
        a = dx * dx + dy * dy
        c = x * x + y * y - threat.engagement_range_km ** 2
        lo, hi = 0.0, 1.0
        if a == 0:
            if c > 0:
                continue
        else:
            b = 2 * (x * dx + y * dy)
            discriminant = b * b - 4 * a * c
            if discriminant <= 0:
                continue
            root = math.sqrt(discriminant)
            lo = max(lo, (-b - root) / (2 * a))
            hi = min(hi, (-b + root) / (2 * a))
        dz = right.alt_ft - left.alt_ft
        if dz == 0:
            if not threat.min_alt_ft <= left.alt_ft <= threat.max_alt_ft:
                continue
        else:
            bounds = sorted(((threat.min_alt_ft - left.alt_ft) / dz,
                             (threat.max_alt_ft - left.alt_ft) / dz))
            lo, hi = max(lo, bounds[0]), min(hi, bounds[1])
        total += max(0.0, hi - lo) * minutes / 60.0
    return total


def build_problem(payload: EvaluatePlanMonteCarloInput, ctx: ToolContext) -> Problem:
    if not payload.plan_id.strip():
        raise ToolInputError("plan_id must not be empty")
    if payload.n_runs <= 0:
        raise ToolInputError("n_runs must be positive")
    if payload.seed < 0:
        raise ToolInputError("seed must be nonnegative")
    try:
        plan = ctx.plans.get(payload.plan_id).model_copy(deep=True)
    except KeyError as exc:
        raise ToolInputError(f"Unknown plan_id: {payload.plan_id}") from exc
    if not math.isfinite(plan.kpis.value):
        raise ToolInputError("plan.kpis.value must be finite")
    scenario = ctx.scenario.snapshot()
    missions = {m.mission_id: m for m in scenario.missions}
    aircraft = {a.tail_no: a for a in scenario.aircraft}
    configs = {c.config_id: c for c in scenario.load_configs}
    weapons = {w.weapon_code: w for w in scenario.weapons}
    bases = {b.base_id: b for b in scenario.bases}
    targets = {t.target_id: t for t in scenario.targets}
    selected = {a.mission_id for a in plan.assignments} | {u.mission_id for u in plan.unassigned}
    assigned_tails = {a.tail_no for a in plan.assignments}
    seen: set[tuple[str, str]] = set()
    visiting: set[str] = set()
    visited: set[str] = set()

    def check_dependencies(mid: str) -> None:
        if mid not in missions:
            raise ToolInputError(f"Unknown mission: {mid}")
        if mid in visiting:
            raise ToolInputError(f"Cyclic mission dependencies at {mid}")
        if mid in visited:
            return
        visiting.add(mid)
        for dependency in missions[mid].dependencies:
            check_dependencies(dependency)
        visiting.remove(mid)
        visited.add(mid)

    for mid in sorted(selected):
        check_dependencies(mid)
    # Include unplanned dependencies as zero-success missions. Missing SEAD
    # leaves threats unsuppressed; missing mandatory support prevents success.
    selected.update(visited)
    for mid in sorted(selected):
        if not math.isfinite(missions[mid].value) or missions[mid].value < 0:
            raise ToolInputError(f"Mission {mid} value must be finite and nonnegative")
        _time(missions[mid].window_start)
        _time(missions[mid].window_end)
    sorties = []
    threats = tuple(t.model_copy(deep=True) for t in sorted(scenario.threats, key=lambda t: t.threat_id))
    for assignment in sorted(plan.assignments, key=lambda a: (a.depart, a.mission_id, a.tail_no)):
        key = (assignment.mission_id, assignment.tail_no)
        if key in seen:
            raise ToolInputError(f"Duplicate aircraft assignment: {key}")
        seen.add(key)
        mission = missions[assignment.mission_id]
        if assignment.tail_no not in aircraft:
            raise ToolInputError(f"Unknown aircraft: {assignment.tail_no}")
        primary = aircraft[assignment.tail_no]
        config = configs.get(assignment.config_id)
        if config is None and mission.type in {"STRIKE", "SEAD", "CAS", "DCA"}:
            raise ToolInputError(f"Unknown load configuration: {assignment.config_id}")
        if config is not None and config.aircraft_type != primary.type:
            raise ToolInputError(f"Load configuration {config.config_id} is incompatible with {primary.tail_no}")
        depart, tot = _time(assignment.depart), _time(assignment.tot)
        if tot < depart:
            raise ToolInputError(f"Mission {mission.mission_id} TOT precedes departure")
        legs = [leg for leg in plan.legs if (leg.mission_id, leg.tail_no) == key]
        slots = [slot for slot in plan.sortie_slots if (slot.mission_id, slot.tail_no) == key]
        if any(_time(slot.end) <= _time(slot.start) for slot in slots):
            raise ToolInputError(f"Invalid sortie slot for {key}")
        paths = [leg.waypoints for leg in legs if leg.waypoints]
        if not paths:
            if primary.base_id not in bases:
                raise ToolInputError(f"Unknown launch base: {primary.base_id}")
            base = bases[primary.base_id]
            target = targets.get(mission.target_id)
            if mission.target_id is not None and target is None:
                raise ToolInputError(f"Unknown target: {mission.target_id}")
            lat, lon = (target.lat, target.lon) if target else (base.lat, base.lon)
            altitude = int(min(primary.ceiling_ft, 20000))
            # When no flown leg exists, fly a straight outbound/return route and
            # hold at the destination for the mission duration.
            finish = assignment.tot + timedelta(minutes=mission.duration_min)
            paths = [[Waypoint(lat=base.lat, lon=base.lon, alt_ft=altitude, eta=assignment.depart),
                      Waypoint(lat=lat, lon=lon, alt_ft=altitude, eta=assignment.tot),
                      Waypoint(lat=lat, lon=lon, alt_ft=altitude, eta=finish),
                      Waypoint(lat=base.lat, lon=base.lon, alt_ft=altitude,
                               eta=finish + (assignment.tot - assignment.depart))]]
        for path in paths:
            if any(_time(right.eta) < _time(left.eta) for left, right in zip(path, path[1:])):
                raise ToolInputError("Flight-leg waypoint ETAs must be nondecreasing")
            if _time(path[0].eta) < depart:
                raise ToolInputError(f"Flight leg starts before departure for {key}")
        end = max(tot + mission.duration_min,
                  *(_time(path[-1].eta) for path in paths),
                  *(_time(slot.end) for slot in slots))
        guidance = "NONE"
        if config is not None:
            for loads in config.stations.values():
                for token in loads:
                    code = token.rsplit("x", 1)[0]
                    if code not in weapons:
                        raise ToolInputError(f"Unknown weapon in {config.config_id}: {code}")
                    if weapons[code].guidance == "LASER":
                        guidance = "LASER"
                    elif guidance == "NONE":
                        guidance = weapons[code].guidance
        route = [LatLon(lat=w.lat, lon=w.lon) for path in paths for w in path]
        p_go = _probability(ctx.providers.weather_go.p_go(
            primary.base_id, route,
            TimeWindow(start=assignment.depart, end=assignment.depart + timedelta(minutes=end - depart)),
            guidance), "weather_go.p_go")
        # Non-weapon missions succeed by completing the sortie; they need no
        # synthetic weapon configuration or call to a weapon-effect provider.
        p_effect = (1.0 if mission.required_effect == "OBSERVE" else
                    _probability(ctx.providers.pk.pk(assignment.config_id, mission.mission_id), "pk.pk"))
        candidates = [primary] + sorted(
            (a for a in scenario.aircraft if a.tail_no not in assigned_tails
             and a.base_id == primary.base_id and a.type == primary.type
             and set(mission.required_capabilities).issubset(a.capabilities)),
            key=lambda a: a.tail_no)
        options = tuple(AircraftOption(
            a.tail_no,
            _probability(ctx.providers.availability.p_fmc(a.tail_no, assignment.depart), "availability.p_fmc"),
            _probability(ctx.providers.component_risk.aircraft_risk(a.tail_no, (end - depart) / 60),
                         "component_risk.aircraft_risk"),
            float(a.turnaround_min)) for a in candidates)
        exposures = tuple((t.threat_id, hours) for path in paths for t in threats
                          if (hours := exposure_hours(path, t)) > 0)
        sorties.append(Sortie(mission.mission_id, depart, tot, end,
                             assignment.depart.date().isoformat(), options, p_go, p_effect,
                             exposures, any(leg.needs_aar for leg in legs)
                             or any(slot.needs_aar for slot in slots)))
    return Problem(tuple(missions[mid].model_copy(deep=True) for mid in sorted(selected)),
                   tuple(sorties), threats, float(plan.kpis.value), payload.n_runs, payload.seed, payload.stress)
