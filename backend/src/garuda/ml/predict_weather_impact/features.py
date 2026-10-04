"""Forecast aggregation shared by training and inference; no network or store writes."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from garuda.bases import base_by_icao
from garuda.common import as_utc
from garuda.contracts.tool import ToolInputError
from garuda.generators.weather import crosswind_kts, snap_half
from garuda.ml.predict_weather_impact.schemas import PredictWeatherImpactInput
from garuda.queries.weather import _haversine_km, _nearest_hour, _samples
from garuda.schemas.aircraft import TYPE_ALIASES
from garuda.schemas.mission import GeneratedScenario, Mission
from garuda.schemas.weather import WeatherCell

MISSION_TYPES = ("DCA", "CAP", "STRIKE", "SEAD", "CAS", "ISR", "AAR", "AIRLIFT", "SAR")
GUIDANCE_TYPES = ("LASER", "GPS", "IR", "NONE")
FEATURE_NAMES = (
    *(
        f"{phase}_{name}"
        for phase in ("launch", "recovery")
        for name in (
            "ceiling_ft",
            "vis_km",
            "crosswind_kts",
            "precip_mm_h",
            "flyability",
        )
    ),
    "route_min_flyability",
    "route_cb_fraction",
    "route_max_headwind_kts",
    "route_icing",
    "target_ceiling_ft",
    "target_vis_km",
    "target_cloud_low_pct",
    "target_precip_mm_h",
    "lead_hours",
    "missing_fraction",
    "local_hour_sin",
    "local_hour_cos",
    "cruise_kts",
    *(f"mission_{name}" for name in MISSION_TYPES),
    *(f"guidance_{name}" for name in GUIDANCE_TYPES),
)


@dataclass(frozen=True)
class Flight:
    route: tuple[tuple[float, float], ...]
    target: tuple[float, float]
    target_hours: float
    recovery_hours: float
    cruise_kts: float
    mission: Mission | None
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeatherFeatures:
    values: dict[str, float]
    limiting_factors: tuple[str, ...]

    def vector(self) -> list[float]:
        return [self.values[name] for name in FEATURE_NAMES]


def resolve_flight(
    payload: PredictWeatherImpactInput, snapshot: GeneratedScenario
) -> Flight:
    bases = {base.base_id: base for base in snapshot.bases}
    for name in (payload.launch_base, payload.recovery_base or payload.launch_base):
        if name not in bases:
            raise ToolInputError(
                f"unknown base {name!r}; use a base_id from the scenario"
            )
    aircraft_name = TYPE_ALIASES.get(payload.aircraft_type, payload.aircraft_type)
    aircraft = next(
        (item for item in snapshot.aircraft_types if item.type == aircraft_name), None
    )
    if aircraft is None:
        raise ToolInputError(f"unknown aircraft_type {payload.aircraft_type!r}")
    mission = None
    if payload.mission_id is not None:
        mission = next(
            (
                item
                for item in snapshot.missions
                if item.mission_id == payload.mission_id
            ),
            None,
        )
        if mission is None:
            raise ToolInputError(f"unknown mission_id {payload.mission_id!r}")
    if payload.route is not None and not payload.route:
        raise ToolInputError("route must contain at least one point when supplied")
    launch = bases[payload.launch_base]
    recovery = bases[payload.recovery_base or payload.launch_base]
    origin, destination = (launch.lat, launch.lon), (recovery.lat, recovery.lon)
    route = [(point.lat, point.lon) for point in payload.route] if payload.route else []
    target = route[-1] if route else origin
    flags = []
    if mission is not None and mission.target_id is not None:
        record = next(
            (item for item in snapshot.targets if item.target_id == mission.target_id),
            None,
        )
        if record is None:
            raise ToolInputError(
                f"mission target {mission.target_id!r} is missing from the scenario"
            )
        target = (record.lat, record.lon)
    elif mission is not None and mission.area_wkt:
        from shapely import wkt

        area = wkt.loads(mission.area_wkt)
        if area.is_empty:
            raise ToolInputError("mission area_wkt must not be empty")
        target = (area.centroid.y, area.centroid.x)
    elif not route:
        flags.append("base_only_assessment")
    if not route:
        route = [origin, target]
    elif route[0] != origin:
        route.insert(0, origin)
    samples = _samples(route)
    target_sample = min(
        samples, key=lambda item: _haversine_km(item[0], item[1], *target)
    )
    if (
        payload.route
        and _haversine_km(target_sample[0], target_sample[1], *target) > 25
    ):
        raise ToolInputError(
            "the supplied route must pass within 25 km of the mission target/area"
        )
    target_hours = target_sample[2] / (aircraft.cruise_kts * 1.852)
    # Preserve a target co-located with recovery when the final leg is diverted.
    if route[-1] != destination or route[-1] == target:
        route.append(destination)
    transit_hours = _samples(route)[-1][2] / (aircraft.cruise_kts * 1.852)
    recovery_hours = max(transit_hours, mission.duration_min / 60 if mission else 0)
    return Flight(
        tuple(route),
        target,
        target_hours,
        recovery_hours,
        aircraft.cruise_kts,
        mission,
        tuple(flags),
    )


class WeatherIndex:
    """Use only forecasts already issued at scenario_time, on the existing 0.5° grid."""

    def __init__(
        self, cells: list[WeatherCell], as_of: datetime, climatology: WeatherCell
    ):
        self.as_of = as_utc(as_of)
        self.climatology = climatology
        self.cells: dict[tuple[float, float, datetime], WeatherCell] = {}
        for cell in cells:
            if as_utc(cell.issued_at) > self.as_of:
                continue
            key = (round(cell.lat, 1), round(cell.lon, 1), as_utc(cell.time))
            previous = self.cells.get(key)
            if previous is None or as_utc(cell.issued_at) > as_utc(previous.issued_at):
                self.cells[key] = cell

    def lookup(
        self, point: tuple[float, float], moment: datetime
    ) -> tuple[WeatherCell, bool]:
        key = (snap_half(point[0]), snap_half(point[1]), _nearest_hour(moment))
        cell = self.cells.get(key)
        return (cell, False) if cell is not None else (self.climatology, True)


def features(
    snapshot: GeneratedScenario,
    payload: PredictWeatherImpactInput,
    index: WeatherIndex,
    flight: Flight | None = None,
) -> WeatherFeatures:
    flight = flight or resolve_flight(payload, snapshot)
    bases = {base.base_id: base for base in snapshot.bases}
    start = as_utc(payload.start_time)
    recovery = start + timedelta(hours=flight.recovery_hours)
    values: dict[str, float] = {}
    factors = list(flight.flags)
    missing = 0
    count = 0
    leads = []

    def lookup(point, moment):
        nonlocal missing, count
        cell, fallback = index.lookup(point, moment)
        missing += int(fallback)
        count += 1
        issued = index.as_of if fallback else as_utc(cell.issued_at)
        leads.append(max(0, (as_utc(moment) - issued).total_seconds() / 3600))
        return cell

    for phase, base_id, moment in (
        ("launch", payload.launch_base, start),
        ("recovery", payload.recovery_base or payload.launch_base, recovery),
    ):
        base = bases[base_id]
        cell = lookup((base.lat, base.lon), moment)
        airfield = base_by_icao(base.icao)
        if airfield is None:
            crosswind = max(cell.wind_kts, cell.gust_kts)
            factors.append(f"{phase}_runway_heading_unknown")
        else:
            crosswind = crosswind_kts(
                max(cell.wind_kts, cell.gust_kts),
                cell.wind_dir_deg,
                airfield.runway_true_deg,
            )
        values.update(
            {
                f"{phase}_ceiling_ft": cell.ceiling_ft
                if cell.ceiling_ft is not None
                else 20000.0,
                f"{phase}_vis_km": cell.visibility_km,
                f"{phase}_crosswind_kts": crosswind,
                f"{phase}_precip_mm_h": cell.precip_mm_h,
                f"{phase}_flyability": cell.flyability,
            }
        )
        if cell.visibility_km < 3:
            factors.append(f"{phase}_visibility")
        if cell.ceiling_ft is not None and cell.ceiling_ft < 1500:
            factors.append(f"{phase}_ceiling")
        if crosswind > 25:
            factors.append(f"{phase}_crosswind")
        if cell.precip_mm_h > 5:
            factors.append(f"{phase}_precipitation")

    samples = _samples(list(flight.route))
    route_cells = []
    headwinds = []
    for i, (lat, lon, distance) in enumerate(samples):
        moment = start + timedelta(hours=distance / (flight.cruise_kts * 1.852))
        # Mission time beyond travel time is spent before the return leg.
        if distance > flight.target_hours * flight.cruise_kts * 1.852:
            transit = samples[-1][2] / (flight.cruise_kts * 1.852)
            moment += timedelta(hours=max(0, flight.recovery_hours - transit))
        cell = lookup((lat, lon), moment)
        route_cells.append(cell)
        a, b = (
            (samples[i - 1], samples[i])
            if i
            else (samples[0], samples[min(1, len(samples) - 1)])
        )
        heading = _bearing(a[:2], b[:2])
        headwinds.append(
            max(
                0.0, cell.wind_kts * math.cos(math.radians(cell.wind_dir_deg - heading))
            )
        )
    values.update(
        {
            "route_min_flyability": min(cell.flyability for cell in route_cells),
            "route_cb_fraction": sum(cell.cb_flag for cell in route_cells)
            / len(route_cells),
            "route_max_headwind_kts": max(headwinds),
            "route_icing": float(
                any(
                    -12 <= cell.temp_c <= 0
                    and cell.cloud_low_pct + cell.cloud_mid_pct >= 50
                    for cell in route_cells
                )
            ),
        }
    )
    if values["route_cb_fraction"] > 0:
        factors.append("route_cb")
    if values["route_icing"]:
        factors.append("route_icing")
    if values["route_min_flyability"] < 0.5:
        factors.append("route_flyability")
    if values["route_max_headwind_kts"] > flight.cruise_kts * 0.25:
        factors.append("route_headwind")
    target = lookup(flight.target, start + timedelta(hours=flight.target_hours))
    values.update(
        {
            "target_ceiling_ft": target.ceiling_ft
            if target.ceiling_ft is not None
            else 20000.0,
            "target_vis_km": target.visibility_km,
            "target_cloud_low_pct": target.cloud_low_pct,
            "target_precip_mm_h": target.precip_mm_h,
        }
    )
    visual = payload.weapon_guidance in {"LASER", "IR"} or (
        payload.weapon_guidance == "NONE"
        and flight.mission is not None
        and flight.mission.type in {"STRIKE", "CAS"}
    )
    if visual and values["target_ceiling_ft"] < 1500:
        factors.append("target_ceiling")
    if visual and target.visibility_km < 3:
        factors.append("target_visibility")
    if payload.weapon_guidance in {"LASER", "IR"} and target.cloud_low_pct >= 50:
        factors.append("guidance_cloud")
    local_hour = (start.hour + start.minute / 60 + flight.target[1] / 15) % 24
    values.update(
        {
            "lead_hours": max(leads),
            "missing_fraction": missing / count,
            "local_hour_sin": math.sin(2 * math.pi * local_hour / 24),
            "local_hour_cos": math.cos(2 * math.pi * local_hour / 24),
            "cruise_kts": flight.cruise_kts,
            **{
                f"mission_{name}": float(
                    flight.mission is not None and flight.mission.type == name
                )
                for name in MISSION_TYPES
            },
            **{
                f"guidance_{name}": float(payload.weapon_guidance == name)
                for name in GUIDANCE_TYPES
            },
        }
    )
    if missing:
        factors.extend(("missing_forecast_climatology", "low_confidence"))
    if values["lead_hours"] >= 24:
        factors.append("forecast_uncertainty")
    return WeatherFeatures(values, tuple(dict.fromkeys(factors)))


def _bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    delta = math.radians(b[1] - a[1])
    return (
        math.degrees(
            math.atan2(
                math.sin(delta) * math.cos(lat2),
                math.cos(lat1) * math.sin(lat2)
                - math.sin(lat1) * math.cos(lat2) * math.cos(delta),
            )
        )
        % 360
    )
