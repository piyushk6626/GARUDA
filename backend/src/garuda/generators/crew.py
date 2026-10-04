"""Synthetic crew roster and a simplified fatigue score for the T0 snapshot.

Callsigns are pseudonymous. The reservoir update follows the fatigue note in
the optimization layer and is a seed score, not a calibrated operational model.
"""

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field

from garuda.common import as_utc as _as_utc
from garuda.config import ScenarioConfig
from garuda.generators.aircraft import allocate_counts, build_aircraft
from garuda.schemas.aircraft import Aircraft, AircraftTypeName, Squadron
from garuda.schemas.crew import Crew, CrewDutyLog, CrewRole, Currency, MedicalStatus

CATALOG_PATH = Path(__file__).resolve().parents[4] / "config" / "crew_types.yaml"
EXPERIENCE_STREAM = 31
MEDICAL_STREAM = 32
QUALIFICATION_STREAM = 33
DUTY_STREAM = 34
R_MAX = 1.0
AWAKE_DRAIN = 0.02
SLEEP_RECOVERY = 0.10
CIRCADIAN = 0.05
PHI_HOUR = 17.0
TOT_PER_HOUR = 0.006
KOLKATA_OFFSET_HOURS = 5.5


class CrewTypeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staffing: dict[CrewRole, int]
    qualifications: list[str] = Field(min_length=1)
    secondary_types: list[AircraftTypeName]
    night_capable: bool
    aar_capable: bool


class CrewCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qualification_window_days: int = Field(gt=0)
    night_window_days: int = Field(gt=0)
    aar_window_days: int = Field(gt=0)
    types: dict[AircraftTypeName, CrewTypeSpec]


def load_crew_catalog(path: Path | None = None) -> CrewCatalog:
    catalog_path = path or CATALOG_PATH
    raw = yaml.safe_load(catalog_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"crew catalog must be a mapping: {catalog_path}")
    return CrewCatalog.model_validate(raw)


def fatigue_score(duty_hours: float, rest_hours: float, at: datetime) -> float:
    """Reservoir, circadian, and time-on-task score in [0, 1] at `at`."""
    if duty_hours < 0 or rest_hours < 0:
        raise ValueError("duty and rest hours must be non-negative")
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    steps = _timeline(duty_hours, rest_hours)
    reservoir = 0.8
    duty_run = 0.0
    end = at.astimezone(timezone.utc)
    start = end - timedelta(hours=len(steps))
    effectiveness = 0.0
    for index, kind in enumerate(steps):
        moment = start + timedelta(hours=index + 1)
        if kind == "sleep":
            reservoir = min(R_MAX, reservoir + SLEEP_RECOVERY * (R_MAX - reservoir))
            duty_run = 0.0
        else:
            reservoir = max(0.0, reservoir - AWAKE_DRAIN)
            duty_run = duty_run + 1.0 if kind == "duty" else 0.0
        local_hour = (moment.hour + moment.minute / 60.0 + KOLKATA_OFFSET_HOURS) % 24.0
        circadian = math.cos(2.0 * math.pi * (local_hour - PHI_HOUR) / 24.0)
        effectiveness = (reservoir / R_MAX) * (1.0 + CIRCADIAN * circadian) - TOT_PER_HOUR * duty_run
    clipped = min(1.0, max(0.0, effectiveness))
    return round(1.0 - clipped, 4)


def fatigue_band(score: float) -> str:
    if score < 0 or score > 1:
        raise ValueError("fatigue score must be in [0, 1]")
    if score > 0.7:
        return "RED"
    if score >= 0.4:
        return "AMBER"
    return "GREEN"


def build_crew(
    config: ScenarioConfig,
    seed: int,
    aircraft: list[Aircraft] | None = None,
    squadrons: list[Squadron] | None = None,
    catalog: CrewCatalog | None = None,
) -> tuple[list[Crew], list[CrewDutyLog]]:
    roster_catalog = catalog or load_crew_catalog()
    if aircraft is None or squadrons is None:
        _types, squadrons, aircraft, _history = build_aircraft(config, seed)
    start = _as_utc(config.start)
    jobs = _jobs(aircraft, roster_catalog)
    expected = int(round(sum(config.fleet.values()) * config.crew_per_airframe))
    if len(jobs) != expected:
        raise ValueError(f"staffing produces {len(jobs)} crew, not {expected}")
    experience = np.random.default_rng(np.random.SeedSequence([seed, EXPERIENCE_STREAM]))
    medical = np.random.default_rng(np.random.SeedSequence([seed, MEDICAL_STREAM]))
    qualifications = np.random.default_rng(np.random.SeedSequence([seed, QUALIFICATION_STREAM]))
    duty = np.random.default_rng(np.random.SeedSequence([seed, DUTY_STREAM]))
    medical_status = _shuffled_labels(
        len(jobs),
        ("FIT", "LIMITED", "GROUNDED"),
        (0.93, 0.05, 0.02),
        medical,
    )
    leave_flags = _binary_flags(len(jobs), 0.05, medical)
    secondary_flags = _secondary_flags(jobs, roster_catalog, qualifications)
    states = [
        _crew_member(
            index,
            job,
            roster_catalog,
            start,
            medical_status[index],
            leave_flags[index],
            secondary_flags[index],
            experience,
            qualifications,
            duty,
        )
        for index, job in enumerate(jobs)
    ]
    _assign_feature_currency(
        states,
        start,
        qualifications,
        attr="night",
        capable=lambda member: roster_catalog.types[member.primary_type].night_capable,
        share=0.70,
        window_days=roster_catalog.night_window_days,
    )
    _assign_feature_currency(
        states,
        start,
        qualifications,
        attr="aar",
        capable=lambda member: roster_catalog.types[member.primary_type].aar_capable,
        share=0.60,
        window_days=roster_catalog.aar_window_days,
    )
    _mark_flight_leads(states)
    logs = [
        CrewDutyLog(
            time=member.updated_at,
            crew_id=member.crew_id,
            duty_hours_24h=member.duty_hours_24h,
            fatigue_score=member.fatigue_score,
        )
        for member in states
    ]
    return states, logs


def _jobs(aircraft: list[Aircraft], catalog: CrewCatalog) -> list[tuple[Aircraft, CrewRole]]:
    jobs: list[tuple[Aircraft, CrewRole]] = []
    by_type: dict[str, list[Aircraft]] = {}
    for airframe in aircraft:
        by_type.setdefault(airframe.type, []).append(airframe)
    for name in sorted(by_type):
        spec = catalog.types[name]
        for airframe in sorted(by_type[name], key=lambda item: item.tail_no):
            for role, count in sorted(spec.staffing.items()):
                jobs.extend((airframe, role) for _ in range(count))
    return jobs


def _secondary_flags(
    jobs: list[tuple[Aircraft, CrewRole]],
    catalog: CrewCatalog,
    rng: np.random.Generator,
) -> list[bool]:
    eligible = [
        index
        for index, (airframe, role) in enumerate(jobs)
        if _legal_secondary_types(airframe.type, role, catalog)
    ]
    target, _rest = allocate_counts(len(jobs), (0.20, 0.80))
    if len(eligible) < target:
        raise ValueError("not enough crew can take a legal secondary type")
    chosen = set(int(index) for index in rng.choice(eligible, size=target, replace=False))
    return [index in chosen for index in range(len(jobs))]


def _legal_secondary_types(
    primary: str,
    role: CrewRole,
    catalog: CrewCatalog,
) -> list[str]:
    spec = catalog.types[primary]
    return [
        name
        for name in spec.secondary_types
        if role in catalog.types[name].staffing and catalog.types[name].staffing[role] > 0
    ]


def _crew_member(
    index: int,
    job: tuple[Aircraft, CrewRole],
    catalog: CrewCatalog,
    start: datetime,
    medical_status: MedicalStatus,
    on_leave: bool,
    takes_secondary: bool,
    experience: np.random.Generator,
    qualifications: np.random.Generator,
    duty: np.random.Generator,
) -> Crew:
    airframe, role = job
    spec = catalog.types[airframe.type]
    total = float(experience.gamma(3.0, 900.0))
    recent_cap = min(90.0, total)
    recent = total if recent_cap <= 5 else float(experience.uniform(5.0, recent_cap))
    recent = min(recent, total)
    qualification_map = {airframe.type: list(spec.qualifications)}
    if takes_secondary:
        options = _legal_secondary_types(airframe.type, role, catalog)
        secondary = str(qualifications.choice(options))
        qualification_map[secondary] = list(catalog.types[secondary].qualifications)
    by_type = {
        name: start - timedelta(days=int(qualifications.integers(1, catalog.qualification_window_days)))
        for name in qualification_map
    }
    on_duty = (index + 1) % 10 == 0
    duty_hours = 10.0 if on_duty else 0.0
    rest_hours = 0.0 if on_duty else float(duty.uniform(12.0, 24.0))
    score = fatigue_score(duty_hours, rest_hours, start)
    number = index + 1
    return Crew(
        crew_id=f"CR-{number:04d}",
        callsign=f"CS-{number:04d}",
        role=role,
        sqn_id=airframe.sqn_id,
        base_id=airframe.base_id,
        primary_type=airframe.type,
        qualifications=qualification_map,
        currency=Currency(by_type=by_type, night=None, aar=None),
        flight_hours_total=total,
        flight_hours_last_30d=recent,
        duty_hours_24h=duty_hours,
        rest_hours_since_last_duty=rest_hours,
        fatigue_score=score,
        medical_status=medical_status,
        leave_until=start + timedelta(days=2) if on_leave else None,
        current_assignment=None,
        is_flight_lead=False,
        updated_at=start,
    )


def _assign_feature_currency(
    crew: list[Crew],
    start: datetime,
    rng: np.random.Generator,
    *,
    attr: str,
    capable,
    share: float,
    window_days: int,
) -> None:
    indexes = [index for index, member in enumerate(crew) if capable(member)]
    if not indexes:
        return
    positive, _absent = allocate_counts(len(indexes), (share, 1.0 - share))
    chosen: set[int] = set()
    if positive:
        picked = rng.choice(indexes, size=positive, replace=False)
        chosen = {int(index) for index in picked}
    for index in indexes:
        if index not in chosen:
            continue
        moment = start - timedelta(days=int(rng.integers(1, window_days)))
        currency = crew[index].currency.model_copy(update={attr: moment})
        crew[index] = crew[index].model_copy(update={"currency": currency})


def _mark_flight_leads(crew: list[Crew]) -> None:
    grouped: dict[str, list[Crew]] = {}
    for member in crew:
        if member.role != "PILOT":
            continue
        grouped.setdefault(member.primary_type, []).append(member)
    leaders: set[str] = set()
    for pilots in grouped.values():
        ranked = sorted(pilots, key=lambda member: (-member.flight_hours_total, member.crew_id))
        take = int(round(len(ranked) * 0.25))
        leaders.update(member.crew_id for member in ranked[:take])
    for index, member in enumerate(crew):
        if member.crew_id in leaders:
            crew[index] = member.model_copy(update={"is_flight_lead": True})


def _shuffled_labels(
    total: int,
    labels: tuple[str, ...],
    weights: tuple[float, ...],
    rng: np.random.Generator,
) -> list[str]:
    counts = allocate_counts(total, weights)
    values: list[str] = []
    for label, count in zip(labels, counts):
        values.extend([label] * count)
    rng.shuffle(values)
    return values


def _binary_flags(total: int, share: float, rng: np.random.Generator) -> list[bool]:
    positive, _negative = allocate_counts(total, (share, 1.0 - share))
    flags = [True] * positive + [False] * (total - positive)
    rng.shuffle(flags)
    return flags


def _timeline(duty_hours: float, rest_hours: float) -> list[str]:
    steps = ["awake"] * 24
    if duty_hours > 0:
        duty_steps = min(24, int(round(duty_hours)))
        off = 24 - duty_steps
        sleep = min(8, off)
        for index in range(sleep):
            steps[index] = "sleep"
        for index in range(24 - duty_steps, 24):
            steps[index] = "duty"
        return steps
    rest_steps = min(24, int(round(rest_hours)))
    sleep = min(8, rest_steps)
    rest_start = 24 - rest_steps
    for index in range(rest_start, rest_start + sleep):
        steps[index] = "sleep"
    for index in range(rest_start + sleep, 24):
        steps[index] = "awake"
    return steps
