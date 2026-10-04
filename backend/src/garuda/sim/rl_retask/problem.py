"""build_problem(payload, ctx) -> RetaskProblem (data gathering only).

The event displaces sorties that have not departed. Each displaced sortie, and each slot of a new
P1 mission, becomes a slot to refill. Candidates are tails that can still fly. A slot and candidate
pair is feasible when the tail has the capabilities and a load config, can reach the target in time
and within range, is free of its other sorties, and the straight path from its base stays under the
risk cap. Airborne sorties and untouched sorties are kept as they are.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from shapely import wkt as shapely_wkt

from garuda.common import as_utc
from garuda.contracts.events import Event
from garuda.contracts.plan import Assignment, PlanDraft, ReasonCode
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.optimization.common.geo import haversine_km, travel_time_min
from garuda.sim.common import Point, ThreatRing, launch_probability, loss_probability, threat_rings
from garuda.sim.rl_retask.schemas import RlRetaskInput

MAX_CANDIDATES = 32
RISK_CAP = 0.35
PREP_MIN = 30.0
AAR_RANGE_FACTOR = 1.6
DEFAULT_MISSION_MIN = 60
DEFAULT_WEATHER_HOURS = 3.0
DEFAULT_SAM_PK = 0.6
DEFAULT_SAM_CONFIDENCE = 0.8
FLYABLE = frozenset({"FMC", "PMC", "NMC", "AOG"})
CONFIG_ROLE = {"STRIKE": "STRIKE", "SEAD": "SEAD", "CAS": "CAS", "ISR": "ISR", "DCA": "DCA", "CAP": "DCA"}
NO_CONFIG = "CLEAN"
REASON_ORDER: tuple[ReasonCode, ...] = ("WEATHER", "NO_TANKER", "THREAT_RISK", "WINDOW", "NO_AIRCRAFT")


@dataclass(frozen=True)
class Slot:
    mission_id: str
    tot: datetime
    value: float
    priority: int
    needs_aar: bool = False
    old: Assignment | None = None


@dataclass(frozen=True)
class Candidate:
    tail_no: str
    aircraft_type: str
    base_id: str
    lat: float
    lon: float
    fuel_fraction: float
    is_fmc: bool


@dataclass
class DecisionInstance:
    """What the policy sees: one row per slot, one column per candidate."""

    values: np.ndarray       # (S,)
    priorities: np.ndarray   # (S,)
    feasible: np.ndarray     # (S, A) bool
    p_success: np.ndarray    # (S, A)
    distance_km: np.ndarray  # (S, A)


@dataclass
class RetaskProblem:
    plan: PlanDraft
    event: Event
    slots: list[Slot]
    candidates: list[Candidate]
    instance: DecisionInstance
    depart: list[list[datetime | None]]
    config: list[list[str | None]]
    reasons: list[ReasonCode]
    kept: list[Assignment]
    displaced: list[Assignment]
    target_points: list[Point | None]
    tanker_available: bool = True
    mission_values: dict[str, float] = field(default_factory=dict)


@dataclass
class _EventEffect:
    unavailable_tails: set[str] = field(default_factory=set)
    closed_bases: set[str] = field(default_factory=set)
    extra_rings: list[ThreatRing] = field(default_factory=list)
    weather_window: tuple[datetime, datetime] | None = None
    tanker_lost: bool = False
    new_mission_id: str | None = None


def build_problem(payload: RlRetaskInput, ctx: ToolContext) -> RetaskProblem:
    plan = _load_plan(ctx, payload.plan_id)
    scenario = ctx.scenario.snapshot()
    event = payload.event
    now = as_utc(event.time)
    missions = {m.mission_id: m for m in scenario.missions}
    aircraft = {a.tail_no: a for a in scenario.aircraft}
    bases = {b.base_id: b for b in scenario.bases}
    targets = {t.target_id: t for t in scenario.targets}
    effect = _event_effect(event, scenario, aircraft, bases, missions)
    rings = threat_rings(scenario.threats) + effect.extra_rings
    legs = {(leg.mission_id, leg.tail_no): leg for leg in plan.legs}

    def target_point(mission_id: str) -> Point | None:
        mission = missions.get(mission_id)
        if mission is None:
            return None
        if mission.target_id and mission.target_id in targets:
            target = targets[mission.target_id]
            return target.lat, target.lon
        if mission.area_wkt:
            centre = shapely_wkt.loads(mission.area_wkt).centroid
            return centre.y, centre.x
        return None

    def current_path(assignment: Assignment) -> list[Point]:
        leg = legs.get((assignment.mission_id, assignment.tail_no))
        if leg is not None and len(leg.waypoints) >= 2:
            return [(w.lat, w.lon) for w in leg.waypoints]
        jet = aircraft.get(assignment.tail_no)
        point = target_point(assignment.mission_id)
        base = bases.get(jet.base_id) if jet else None
        return [(base.lat, base.lon), point] if base and point else []

    displaced: list[Assignment] = []
    kept: list[Assignment] = []
    for assignment in plan.assignments:
        future = as_utc(assignment.depart) >= now
        if future and _is_displaced(assignment, effect, aircraft, legs, rings, current_path):
            displaced.append(assignment)
        else:
            kept.append(assignment)

    per_mission = {m: sum(1 for a in plan.assignments if a.mission_id == m) for m in {a.mission_id for a in plan.assignments}}
    slots = [
        Slot(
            mission_id=a.mission_id,
            tot=a.tot,
            value=_mission_value(missions, a.mission_id) / per_mission[a.mission_id],
            priority=_mission_priority(missions, a.mission_id),
            needs_aar=bool((leg := legs.get((a.mission_id, a.tail_no))) and leg.needs_aar),
            old=a,
        )
        for a in displaced
    ]
    if effect.new_mission_id is not None:
        slots.extend(_new_mission_slots(missions[effect.new_mission_id], now))

    pool = [
        Candidate(
            tail_no=jet.tail_no,
            aircraft_type=jet.type,
            base_id=jet.base_id,
            lat=bases[jet.base_id].lat,
            lon=bases[jet.base_id].lon,
            fuel_fraction=jet.fuel_kg / jet.fuel_capacity_kg,
            is_fmc=jet.status == "FMC",
        )
        for jet in scenario.aircraft
        if jet.status in FLYABLE
        and jet.tail_no not in effect.unavailable_tails
        and jet.base_id not in effect.closed_bases
        and jet.base_id in bases
    ]
    durations = {m.mission_id: m.duration_min for m in scenario.missions}
    configs = list(scenario.load_configs)
    points = [target_point(slot.mission_id) for slot in slots]

    def evaluate(slot: Slot, point: Point | None, candidate: Candidate):
        """(p_success, distance, depart, config, failure reason or None) for one pair."""
        jet = aircraft[candidate.tail_no]
        mission = missions.get(slot.mission_id)
        if mission is None or not set(mission.required_capabilities) <= set(jet.capabilities):
            return 0.0, 0.0, None, None, "NO_AIRCRAFT"
        config = _config_for(slot, mission, jet, configs)
        if config is None:
            return 0.0, 0.0, None, None, "NO_AIRCRAFT"
        if effect.weather_window and effect.weather_window[0] <= as_utc(slot.tot) <= effect.weather_window[1]:
            return 0.0, 0.0, None, None, "WEATHER"
        home = (candidate.lat, candidate.lon)
        distance = haversine_km(*home, *point) if point else 0.0
        reach = jet.range_km * (AAR_RANGE_FACTOR if slot.needs_aar and not effect.tanker_lost else 1.0)
        if 2.0 * distance > reach:
            return 0.0, distance, None, None, "NO_TANKER" if effect.tanker_lost else "NO_AIRCRAFT"
        depart = as_utc(slot.tot) - timedelta(minutes=travel_time_min(distance, jet.cruise_kts))
        if depart < now + timedelta(minutes=PREP_MIN):
            return 0.0, distance, None, None, "WINDOW"
        busy_until = as_utc(slot.tot) + timedelta(minutes=durations.get(slot.mission_id, DEFAULT_MISSION_MIN) + jet.turnaround_min)
        if any(_overlaps(k, depart, busy_until, durations) for k in kept if k.tail_no == candidate.tail_no):
            return 0.0, distance, None, None, "NO_AIRCRAFT"
        p_launch = launch_probability(jet, depart)
        if p_launch <= 0:
            return 0.0, distance, None, None, "NO_AIRCRAFT"
        risk = loss_probability([home, point], rings) if point else 0.0
        if risk > RISK_CAP:
            return 0.0, distance, None, None, "THREAT_RISK"
        return p_launch * (1.0 - risk), distance, depart, config, None

    scored = [[evaluate(slot, point, c) for c in pool] for slot, point in zip(slots, points)]
    keep_idx = _shortlist(scored, len(pool))
    candidates = [pool[i] for i in keep_idx]
    rows = [[row[i] for i in keep_idx] for row in scored]
    instance = DecisionInstance(
        values=np.array([s.value for s in slots], dtype=float),
        priorities=np.array([s.priority for s in slots], dtype=float),
        feasible=np.array([[cell[4] is None for cell in row] for row in rows], dtype=bool).reshape(len(slots), len(candidates)),
        p_success=np.array([[cell[0] for cell in row] for row in rows], dtype=float).reshape(len(slots), len(candidates)),
        distance_km=np.array([[cell[1] for cell in row] for row in rows], dtype=float).reshape(len(slots), len(candidates)),
    )
    return RetaskProblem(
        plan=plan,
        event=event,
        slots=slots,
        candidates=candidates,
        instance=instance,
        depart=[[cell[2] for cell in row] for row in rows],
        config=[[cell[3] for cell in row] for row in rows],
        reasons=[_slot_reason(row, effect) for row in scored],
        kept=kept,
        displaced=displaced,
        target_points=points,
        tanker_available=not effect.tanker_lost,
        mission_values={m.mission_id: float(m.value) for m in scenario.missions},
    )


def _load_plan(ctx: ToolContext, plan_id: str) -> PlanDraft:
    try:
        return ctx.plans.get(plan_id)
    except KeyError as exc:
        raise ToolInputError(f"unknown plan_id {plan_id}; pass a plan id returned by a planning tool.") from exc


def _event_effect(event: Event, scenario, aircraft, bases, missions) -> _EventEffect:
    effect = _EventEffect()
    params = event.params
    if event.type in {"aircraft_nmc", "tanker_lost"}:
        if event.entity_id not in aircraft:
            raise ToolInputError(f"{event.type} needs entity_id set to a known tail number; got {event.entity_id!r}.")
        effect.unavailable_tails.add(event.entity_id)
        effect.tanker_lost = event.type == "tanker_lost"
    elif event.type == "base_closed":
        if event.entity_id not in bases:
            raise ToolInputError(f"base_closed needs entity_id set to a known base id; valid: {sorted(bases)}.")
        effect.closed_bases.add(event.entity_id)
    elif event.type == "p1_request":
        if event.entity_id not in missions:
            raise ToolInputError("p1_request needs entity_id set to a mission id from get_mission_requests.")
        effect.new_mission_id = event.entity_id
    elif event.type == "new_sam":
        effect.extra_rings.append(_new_sam_ring(event, scenario))
    elif event.type == "weather_override":
        start = as_utc(_param_time(params, "start", event.time))
        end = as_utc(_param_time(params, "end", start + timedelta(hours=DEFAULT_WEATHER_HOURS)))
        if end <= start:
            raise ToolInputError("weather_override params.end must be after params.start.")
        effect.weather_window = (start, end)
    return effect


def _new_sam_ring(event: Event, scenario) -> ThreatRing:
    known = {t.threat_id: t for t in scenario.threats}
    if event.entity_id in known:
        threat = known[event.entity_id]
        return ThreatRing(threat.threat_id, threat.lat, threat.lon, threat.engagement_range_km,
                          threat.pk_base, threat.confidence, threat.min_alt_ft, threat.max_alt_ft)
    params = event.params
    missing = [key for key in ("lat", "lon", "range_km") if key not in params]
    if missing:
        raise ToolInputError(f"new_sam needs a known threat entity_id or params {missing}.")
    return ThreatRing(
        threat_id=event.entity_id or "NEW-SAM",
        lat=float(params["lat"]),
        lon=float(params["lon"]),
        range_km=float(params["range_km"]),
        pk=float(params.get("pk", DEFAULT_SAM_PK)),
        confidence=float(params.get("confidence", DEFAULT_SAM_CONFIDENCE)),
    )


def _param_time(params: dict, key: str, default: datetime) -> datetime:
    raw = params.get(key)
    if raw is None:
        return default
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise ToolInputError(f"params.{key} must be an ISO time; got {raw!r}.") from exc


def _is_displaced(assignment: Assignment, effect: _EventEffect, aircraft, legs, rings, current_path) -> bool:
    if assignment.tail_no in effect.unavailable_tails:
        return True
    jet = aircraft.get(assignment.tail_no)
    if jet is not None and jet.base_id in effect.closed_bases:
        return True
    if effect.tanker_lost:
        leg = legs.get((assignment.mission_id, assignment.tail_no))
        if leg is not None and leg.needs_aar:
            return True
    if effect.weather_window and effect.weather_window[0] <= as_utc(assignment.tot) <= effect.weather_window[1]:
        return True
    if effect.extra_rings:
        path = current_path(assignment)
        return bool(path) and loss_probability(path, rings) > RISK_CAP
    return False


def _new_mission_slots(mission, now: datetime) -> list[Slot]:
    lead = now + timedelta(hours=1)
    tot = max(as_utc(mission.window_start), lead)
    if tot > as_utc(mission.window_end):
        tot = as_utc(mission.window_end)
    return [
        Slot(mission_id=mission.mission_id, tot=tot, value=mission.value / mission.min_aircraft, priority=1)
        for _ in range(mission.min_aircraft)
    ]


def _mission_value(missions, mission_id: str) -> float:
    mission = missions.get(mission_id)
    return float(mission.value) if mission else 0.0


def _mission_priority(missions, mission_id: str) -> int:
    mission = missions.get(mission_id)
    return mission.priority if mission else 5


def _config_for(slot: Slot, mission, jet, configs) -> str | None:
    if slot.old is not None:
        old = next((c for c in configs if c.config_id == slot.old.config_id), None)
        if old is not None and old.aircraft_type == jet.type:
            return old.config_id
    role = CONFIG_ROLE.get(mission.type)
    if role is None:
        return slot.old.config_id if slot.old is not None else NO_CONFIG
    match = next((c for c in configs if c.aircraft_type == jet.type and c.role == role), None)
    return match.config_id if match else None


def _overlaps(kept: Assignment, start: datetime, end: datetime, durations) -> bool:
    kept_end = as_utc(kept.tot) + timedelta(minutes=durations.get(kept.mission_id, DEFAULT_MISSION_MIN))
    return as_utc(kept.depart) < end and start < kept_end


def _shortlist(scored, n_pool: int) -> list[int]:
    """Keep the candidates with the best feasible success, capped at MAX_CANDIDATES, in pool order."""
    best = [max((row[i][0] for row in scored if row[i][4] is None), default=0.0) for i in range(n_pool)]
    useful = [i for i in range(n_pool) if best[i] > 0]
    useful.sort(key=lambda i: best[i], reverse=True)
    return sorted(useful[:MAX_CANDIDATES])


def _slot_reason(row, effect: _EventEffect) -> ReasonCode:
    found = {cell[4] for cell in row if cell[4] is not None}
    if effect.weather_window and "WEATHER" in found:
        return "WEATHER"
    capable = found - {"NO_AIRCRAFT"}
    return next((reason for reason in REASON_ORDER if reason in capable), "NO_AIRCRAFT")
