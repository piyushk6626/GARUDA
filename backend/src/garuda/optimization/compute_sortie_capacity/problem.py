"""Read a scenario into private, picklable LP parameters; no contract changes."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from math import isfinite

from garuda.contracts.geo import LatLon, TimeWindow
from garuda.contracts.tool import ToolInputError
from garuda.queries.crew import availability_blockers

FUEL_KG_PER_LITRE = 0.8
SURGE_CREW_MULTIPLIER = 1.15
SURGE_TURNAROUND_MULTIPLIER = 0.85
# Seats per sortie, without importing another tool.
SEATS = {
    "Su-30MKI": {"PILOT": 1, "WSO": 1},
    "C-130J": {"PILOT": 1, "LOADMASTER": 1},
    "AEW&C": {"PILOT": 1, "AEW_OP": 1},
    "IL-78": {"PILOT": 2},
}


@dataclass(frozen=True)
class AircraftGroup:
    aircraft_type: str
    turnaround_hours: float
    fuel_kg: float
    available: tuple[float, ...]
    loads: tuple[dict[str, int], ...]
    seats: dict[str, int]


@dataclass(frozen=True)
class CrewResource:
    crew_id: str
    role: str
    eligible: frozenset[tuple[int, int]]  # (group, hour)
    duty_hours: float


@dataclass(frozen=True)
class BaseProblem:
    base_id: str
    horizon_hours: int
    groups: tuple[AircraftGroup, ...]
    ground_hours: float
    fuel_kg: float
    stock: dict[str, int]
    runway_ops: float
    shelters: int
    open_hours: tuple[bool, ...]
    crew: tuple[CrewResource, ...]


@dataclass(frozen=True)
class CapacityProblem:
    bases: tuple[BaseProblem, ...]
    seed: int


def probability(value, label):
    if (
        not isinstance(value, (int, float))
        or not isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ToolInputError(
            f"{label} must return a finite probability between 0 and 1"
        )
    return float(value)


def build_problem(payload, ctx) -> CapacityProblem:
    if payload.start.utcoffset() is None:
        raise ToolInputError("start must be timezone-aware")
    if payload.horizon_hours <= 0:
        raise ToolInputError("horizon_hours must be positive")
    try:
        hours = tuple(
            payload.start + timedelta(hours=p) for p in range(payload.horizon_hours)
        )
        end = hours[-1] + timedelta(hours=1)
    except (OverflowError, ValueError) as exc:
        raise ToolInputError(
            "start and horizon_hours exceed the supported datetime range"
        ) from exc
    scenario = ctx.scenario.snapshot()
    bases = {b.base_id: b for b in scenario.bases}
    if len(bases) != len(scenario.bases):
        raise ToolInputError("scenario contains duplicate base IDs")
    selected = (
        sorted(bases)
        if payload.base_ids is None
        else list(dict.fromkeys(payload.base_ids))
    )
    unknown = sorted(set(selected) - bases.keys())
    if unknown:
        raise ToolInputError("Unknown base_ids: " + ", ".join(unknown))
    if not selected:
        return CapacityProblem((), ctx.seed)

    loads = defaultdict(list)
    categories = {w.weapon_code: w.category for w in scenario.weapons}
    for config in sorted(scenario.load_configs, key=lambda c: c.config_id):
        consumption = Counter()
        for tokens in config.stations.values():
            for token in tokens:
                code, count = token.rsplit("x", 1)
                consumption[code] += int(count)
        # Pods are carried equipment, not expendable munitions.
        consumption = {
            k: v for k, v in consumption.items() if categories.get(k) != "PODS"
        }
        if consumption not in loads[config.aircraft_type]:
            loads[config.aircraft_type].append(consumption)

    stock = {}
    for item in scenario.stocks:
        key = (item.base_id, item.weapon_code)
        if key in stock:
            raise ToolInputError(
                f"Duplicate weapon stock: {item.base_id}:{item.weapon_code}"
            )
        stock[key] = item.quantity - item.reserved_qty

    result = []
    for bid in selected:
        base = bases[bid]
        by_group = defaultdict(list)
        tails = set()
        for aircraft in scenario.aircraft:
            if aircraft.base_id != bid:
                continue
            if aircraft.tail_no in tails:
                raise ToolInputError(f"Duplicate aircraft tail: {aircraft.tail_no}")
            tails.add(aircraft.tail_no)
            by_group[
                (aircraft.type, aircraft.turnaround_min, aircraft.fuel_capacity_kg)
            ].append(aircraft)
        groups = []
        for (kind, minutes, fuel), aircraft in sorted(by_group.items()):
            counts = tuple(
                sum(
                    probability(
                        ctx.providers.availability.p_fmc(a.tail_no, hour),
                        f"availability.p_fmc({a.tail_no})",
                    )
                    for a in sorted(aircraft, key=lambda a: a.tail_no)
                )
                for hour in hours
            )
            groups.append(
                AircraftGroup(
                    kind,
                    minutes
                    / 60
                    * (SURGE_TURNAROUND_MULTIPLIER if payload.surge else 1),
                    fuel,
                    counts,
                    tuple(loads[kind]) or ({},),
                    SEATS.get(kind, {"PILOT": 1}),
                )
            )
        position = LatLon(lat=base.lat, lon=base.lon)
        open_hours = tuple(
            probability(
                ctx.providers.weather_go.p_go(
                    bid,
                    [position],
                    TimeWindow(start=hour, end=min(hour + timedelta(hours=1), end)),
                    "NONE",
                ),
                f"weather_go.p_go({bid})",
            )
            >= 0.5
            for hour in hours
        )
        crew = []
        crew_ids = set()
        for member in sorted(scenario.crew, key=lambda c: c.crew_id):
            if member.base_id != bid:
                continue
            if member.crew_id in crew_ids:
                raise ToolInputError(f"Duplicate crew ID: {member.crew_id}")
            crew_ids.add(member.crew_id)
            eligible = frozenset(
                (g, p)
                for g, group in enumerate(groups)
                if member.role in group.seats
                for p, hour in enumerate(hours)
                if not availability_blockers(
                    member, at=hour, aircraft_type=group.aircraft_type
                )
                and probability(
                    ctx.providers.fatigue.fatigue(member.crew_id, hour),
                    f"fatigue.fatigue({member.crew_id})",
                )
                <= 0.7
            )
            crew.append(
                CrewResource(
                    member.crew_id,
                    member.role,
                    eligible,
                    max(0.0, 14.0 - member.duty_hours_24h),
                )
            )
        result.append(
            BaseProblem(
                bid,
                payload.horizon_hours,
                tuple(groups),
                base.ground_crew_teams
                * (SURGE_CREW_MULTIPLIER if payload.surge else 1),
                base.fuel_capacity_l * FUEL_KG_PER_LITRE,
                {code: qty for (b, code), qty in stock.items() if b == bid},
                2 * base.max_sorties_per_hour,
                base.shelter_count,
                open_hours,
                tuple(crew),
            )
        )
    return CapacityProblem(tuple(result), ctx.seed)
