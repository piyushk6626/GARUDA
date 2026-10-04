"""A two-base, two-target scenario and a two-sortie plan shared by the simulation tool tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from garuda.contracts.geo import Waypoint
from garuda.contracts.plan import Assignment, FlightLeg, PlanDraft
from garuda.schemas.weapon import TARGET_CLASSES

T0 = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)


def aircraft(tail: str, kind: str = "Su-30MKI", base: str = "B1", status: str = "FMC", caps=("STRIKE", "SEAD"), **kw):
    fields = dict(
        tail_no=tail, type=kind, base_id=base, status=status, maint_return_eta=None, capabilities=list(caps),
        fuel_kg=9000.0, fuel_capacity_kg=10_000.0, range_km=3000.0, cruise_kts=480.0, turnaround_min=45,
    )
    fields.update(kw)
    return SimpleNamespace(**fields)


def mission(mid: str, target: str | None, value: float, priority: int = 1, kind: str = "STRIKE", **kw):
    fields = dict(
        mission_id=mid, type=kind, value=value, priority=priority, target_id=target, area_wkt=None,
        required_effect="DESTROY" if target else "OBSERVE", required_capabilities=[kind] if kind in {"STRIKE", "SEAD"} else [],
        min_aircraft=1, dependencies=[], duration_min=60, window_start=T0, window_end=T0 + timedelta(hours=12),
    )
    fields.update(kw)
    return SimpleNamespace(**fields)


def threat(tid: str, lat: float, lon: float, range_km: float = 40.0, pk: float = 0.5, confidence: float = 0.8, status: str = "ACTIVE"):
    return SimpleNamespace(
        threat_id=tid, lat=lat, lon=lon, engagement_range_km=range_km, pk_base=pk, confidence=confidence,
        status=status, min_alt_ft=0, max_alt_ft=60_000,
    )


def scenario(**overrides):
    world = dict(
        bases=[SimpleNamespace(base_id="B1", lat=28.0, lon=77.0), SimpleNamespace(base_id="B2", lat=30.0, lon=75.0)],
        targets=[
            SimpleNamespace(target_id="T1", target_class="RADAR", hardness=0.4, lat=29.0, lon=78.0),
            SimpleNamespace(target_id="T2", target_class="RUNWAY", hardness=0.85, lat=28.5, lon=79.0),
        ],
        weapons=[
            SimpleNamespace(
                weapon_code="W1", category="ASM", guidance="GPS", pk_by_target_class={c: 0.6 for c in TARGET_CLASSES}
            )
        ],
        load_configs=[
            SimpleNamespace(config_id="CFG-SU-STRIKE", aircraft_type="Su-30MKI", role="STRIKE", stations={"S1": ["W1x2"]}),
            SimpleNamespace(config_id="CFG-SU-SEAD", aircraft_type="Su-30MKI", role="SEAD", stations={"S1": ["W1x1"]}),
            SimpleNamespace(config_id="CFG-RF-STRIKE", aircraft_type="Rafale", role="STRIKE", stations={"S1": ["W1x2"]}),
        ],
        aircraft=[
            aircraft("SU-1"),
            aircraft("SU-2"),
            aircraft("SU-3", base="B2", status="PMC"),
            aircraft("RF-1", kind="Rafale", base="B2", caps=("STRIKE",)),
            aircraft("IL-1", kind="IL-78", caps=("AAR_TANKER",)),
        ],
        missions=[mission("M1", "T1", 10.0), mission("M2", "T2", 6.0, priority=2)],
        threats=[],
        cells=[],
    )
    world.update(overrides)
    return SimpleNamespace(**world)


def assignment(tail: str, mid: str, depart_h: float = 2.0, tot_h: float = 3.0, config: str = "CFG-SU-STRIKE") -> Assignment:
    return Assignment(
        tail_no=tail, config_id=config, mission_id=mid,
        depart=T0 + timedelta(hours=depart_h), tot=T0 + timedelta(hours=tot_h),
    )


def leg(tail: str, mid: str, start: tuple[float, float], end: tuple[float, float], needs_aar: bool = False) -> FlightLeg:
    return FlightLeg(
        mission_id=mid,
        tail_no=tail,
        waypoints=[
            Waypoint(lat=start[0], lon=start[1], alt_ft=25_000, eta=T0 + timedelta(hours=2)),
            Waypoint(lat=end[0], lon=end[1], alt_ft=25_000, eta=T0 + timedelta(hours=3)),
        ],
        needs_aar=needs_aar,
        fuel_demand_kg=3000.0 if needs_aar else 0.0,
    )


def plan(assignments=None, legs=None, plan_id: str = "P1") -> PlanDraft:
    if assignments is None:
        assignments = [assignment("SU-1", "M1"), assignment("SU-2", "M2")]
    return PlanDraft(plan_id=plan_id, scenario_time=T0, assignments=assignments, legs=legs or [])


class PlanStore:
    def __init__(self, *plans: PlanDraft) -> None:
        self.plans = {p.plan_id: p for p in plans}

    def get(self, plan_id: str) -> PlanDraft:
        return self.plans[plan_id]

    def put(self, draft: PlanDraft) -> str:
        self.plans[draft.plan_id] = draft
        return draft.plan_id


def ctx(world=None, *plans: PlanDraft, models=None, when: datetime = T0):
    world = world or scenario()
    return SimpleNamespace(
        scenario=SimpleNamespace(snapshot=lambda: world),
        plans=PlanStore(*(plans or (plan(),))),
        models=models or SimpleNamespace(load=_no_model),
        scenario_time=when,
        seed=42,
    )


def _no_model(name, version=None):
    raise KeyError(name)
