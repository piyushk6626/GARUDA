"""Read-only preparation: conservative metric occupancy, integer-minute shifts.

The output supports one altitude per flight. Use 100-foot flight levels at or
above every original waypoint, bounded by the aircraft ceiling; this avoids
inventing terrain clearance for lower altitudes. Times retain milliseconds.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from math import ceil, floor, isfinite

from pyproj import Geod, Transformer
from shapely import wkt
from shapely.geometry import LineString, Point
from shapely.ops import transform
from shapely.strtree import STRtree

from garuda.contracts.plan import PlanDraft
from garuda.contracts.tool import ToolInputError

MINUTE = 60_000
GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True)
class Flight:
    key: tuple[str, str]
    label: str
    weight: int
    lower_shift: int
    upper_shift: int
    level: int
    ceiling: int
    start: int
    end: int
    leg_indices: tuple[int, ...]


@dataclass(frozen=True)
class Segment:
    flight: int
    start: int
    end: int
    geometry: object


@dataclass(frozen=True)
class Pass:
    flight: int
    zone: int
    start: int
    end: int


@dataclass(frozen=True)
class Encounter:
    a: int
    b: int
    start_a: int
    end_a: int
    start_b: int
    end_b: int
    zone: str


@dataclass(frozen=True)
class CivilEncounter:
    flight: int
    label: str
    start: int
    end: int
    civil_start: int
    civil_end: int
    low_ft: float | None
    high_ft: float | None


@dataclass
class AirspaceProblem:
    plan: PlanDraft
    flights: list[Flight]
    zones: list
    passes: list[Pass]
    encounters: list[Encounter]
    civil: list[CivilEncounter]
    max_shift: int
    time_sep: int
    altitude_sep: int
    civil_time_sep: int
    civil_altitude_sep: int
    seed: int

    def ticks(self, moment: datetime) -> int:
        return round((moment - self.plan.scenario_time).total_seconds() * 1000)


def _aware(moment, name):
    if moment.utcoffset() is None:
        raise ToolInputError(f"{name} must be timezone-aware")


def _unique(items, key, name):
    result = {}
    for item in items:
        value = key(item)
        if value in result:
            raise ToolInputError(f"duplicate {name}: {value}")
        result[value] = item
    return result


def _pieces(geometry):
    if hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _pieces(part)
    elif not geometry.is_empty:
        yield geometry


def _intersection_times(segment, geometry):
    """Clip each pass and round outwards to preserve subsecond occupancy."""
    line = segment.geometry
    if line.length == 0:
        if geometry.intersects(line):
            yield segment.start, segment.end
        return
    for part in _pieces(line.intersection(geometry)):
        fractions = [line.project(Point(point), normalized=True) for point in part.coords]
        duration = segment.end - segment.start
        start = floor(segment.start + min(fractions) * duration)
        end = ceil(segment.start + max(fractions) * duration)
        yield start, max(start + 1, end)


def build_problem(payload, ctx) -> AirspaceProblem:
    for name, value in (("max_shift_min", payload.max_shift_min),
                        ("separation.time_min", payload.separation.time_min),
                        ("separation.altitude_ft", payload.separation.altitude_ft),
                        ("separation.lateral_km", payload.separation.lateral_km)):
        if not isfinite(value) or value < 0:
            raise ToolInputError(f"{name} must be finite and non-negative")
    _aware(ctx.scenario_time, "scenario_time")
    try:
        plan = ctx.plans.get(payload.plan_id).model_copy(deep=True)
    except KeyError as exc:
        raise ToolInputError(f"unknown plan_id: {payload.plan_id}") from exc
    if plan.plan_id != payload.plan_id:
        raise ToolInputError("plan store returned a different plan_id")
    if plan.status != "DRAFT":
        raise ToolInputError("deconflict_airspace requires a DRAFT plan")
    _aware(plan.scenario_time, "plan.scenario_time")
    scenario = ctx.scenario.snapshot()
    missions = _unique(scenario.missions, lambda m: m.mission_id, "mission_id")
    aircraft = _unique(scenario.aircraft, lambda a: a.tail_no, "tail_no")
    zones = list(_unique(scenario.zones, lambda z: z.zone_id, "zone_id").values())
    assignments = _unique(plan.assignments, lambda a: (a.mission_id, a.tail_no), "assignment")
    legs = defaultdict(list)
    for index, leg in enumerate(plan.legs):
        key = (leg.mission_id, leg.tail_no)
        if key not in assignments:
            raise ToolInputError(f"route has no assignment: {key}")
        if len(leg.waypoints) < 2:
            raise ToolInputError(f"route needs at least two timed waypoints: {key}")
        unknown = set(leg.zones) - {z.zone_id for z in zones}
        if unknown:
            raise ToolInputError(f"unknown route zones: {sorted(unknown)}")
        for waypoint in leg.waypoints:
            _aware(waypoint.eta, "waypoint.eta")
            if (not all(isfinite(v) for v in (waypoint.lat, waypoint.lon))
                    or not -90 <= waypoint.lat <= 90 or not -180 <= waypoint.lon <= 180):
                raise ToolInputError(f"invalid waypoint coordinates: {key}")
        if any(a.eta >= b.eta for a, b in zip(leg.waypoints, leg.waypoints[1:])):
            raise ToolInputError(f"waypoint ETAs must increase strictly: {key}")
        legs[key].append(index)
    for slot in plan.sortie_slots:
        if (slot.mission_id, slot.tail_no) not in assignments:
            raise ToolInputError(f"sortie slot has no assignment: {slot.slot_id}")
        _aware(slot.start, "sortie.start")
        _aware(slot.end, "sortie.end")
        if slot.end <= slot.start:
            raise ToolInputError(f"sortie end must follow start: {slot.slot_id}")
    slots_by_flight = defaultdict(list)
    for slot in plan.sortie_slots:
        slots_by_flight[slot.mission_id, slot.tail_no].append(slot)

    def ticks(moment):
        return round((moment - plan.scenario_time).total_seconds() * 1000)

    counts = Counter(a.mission_id for a in plan.assignments)
    flights = []
    for assignment in plan.assignments:
        key = assignment.mission_id, assignment.tail_no
        if key not in legs:
            raise ToolInputError(f"assignment has no planned route: {key}; call plan_route first")
        if assignment.mission_id not in missions or assignment.tail_no not in aircraft:
            raise ToolInputError(f"assignment references an unknown mission or aircraft: {key}")
        mission, tail = missions[assignment.mission_id], aircraft[assignment.tail_no]
        for value, name in ((assignment.depart, "assignment.depart"),
                            (assignment.tot, "assignment.tot"),
                            (mission.window_start, "mission.window_start"),
                            (mission.window_end, "mission.window_end")):
            _aware(value, name)
        route = sorted((plan.legs[i] for i in legs[key]), key=lambda leg: leg.waypoints[0].eta)
        if (assignment.tot < assignment.depart or route[0].waypoints[0].eta < assignment.depart
                or any(a.waypoints[-1].eta > b.waypoints[0].eta for a, b in zip(route, route[1:]))):
            raise ToolInputError(f"inconsistent assignment/route times: {key}")
        if not isfinite(tail.ceiling_ft) or tail.ceiling_ft <= 0:
            raise ToolInputError(f"invalid aircraft ceiling: {tail.tail_no}")
        level = ceil(max(w.alt_ft for leg in route for w in leg.waypoints) / 100)
        # Mission ID remains the existing single-flight label. A tail suffix
        # disambiguates multiple aircraft assigned to the same mission.
        label = assignment.mission_id if counts[assignment.mission_id] == 1 else \
            f"{assignment.mission_id}:{assignment.tail_no}"
        slots = slots_by_flight[key]
        start = min([assignment.depart] + [slot.start for slot in slots])
        end = max([route[-1].waypoints[-1].eta, assignment.tot] + [slot.end for slot in slots])
        flights.append(Flight(
            key, label, 6 - mission.priority,
            max(-payload.max_shift_min,
                ceil((mission.window_start - assignment.tot).total_seconds() / 60),
                ceil((ctx.scenario_time - assignment.depart).total_seconds() / 60)),
            min(payload.max_shift_min,
                floor((mission.window_end - assignment.tot).total_seconds() / 60)),
            level, floor(tail.ceiling_ft / 100), ticks(start), ticks(end), tuple(legs[key]),
        ))
    if len({f.label for f in flights}) != len(flights):
        raise ToolInputError("mission/tail identifiers produce ambiguous flight labels")
    problem = AirspaceProblem(
        plan, flights, zones, [], [], [], payload.max_shift_min,
        payload.separation.time_min * MINUTE, payload.separation.altitude_ft,
        max(5, payload.separation.time_min) * MINUTE,
        max(1000, payload.separation.altitude_ft), ctx.seed,
    )
    if not flights:
        return problem
    points = [w for leg in plan.legs for w in leg.waypoints]
    projection = Transformer.from_crs(
        "EPSG:4326", f"+proj=aeqd +lat_0={sum(w.lat for w in points) / len(points)} "
        f"+lon_0={sum(w.lon for w in points) / len(points)} +datum=WGS84 +units=m",
        always_xy=True,
    )
    segments = []
    for index, flight in enumerate(flights):
        for leg_index in flight.leg_indices:
            for a, b in zip(plan.legs[leg_index].waypoints, plan.legs[leg_index].waypoints[1:]):
                start, end = projection.transform(a.lon, a.lat), projection.transform(b.lon, b.lat)
                geometry = Point(start) if start == end else LineString([start, end])
                segments.append(Segment(index, ticks(a.eta), ticks(b.eta), geometry))
    geometries = []
    for zone in zones:
        try:
            geometry = wkt.loads(zone.wkt)
            if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in {
                    "Polygon", "MultiPolygon", "LineString", "MultiLineString"}:
                raise ValueError("expected a valid polygon or corridor line")
            if not all(isfinite(v) for v in geometry.bounds):
                raise ValueError("non-finite coordinates")
            geometry = transform(projection.transform, geometry.segmentize(.01))
            if "LineString" in geometry.geom_type:
                if zone.width_km is None:
                    raise ValueError("corridor line needs width_km")
                geometry = geometry.buffer(zone.width_km * 500)
            geometries.append(geometry.buffer(.01))
        except Exception as exc:
            raise ToolInputError(f"invalid airspace geometry for {zone.zone_id}: {exc}") from exc
    tree = STRtree(geometries)
    raw = defaultdict(list)
    for segment in segments:
        for zone_index in tree.query(segment.geometry, predicate="intersects"):
            for start, end in _intersection_times(segment, geometries[zone_index]):
                raw[segment.flight, int(zone_index)].append((start, end))
    # One flight must not consume capacity twice at an adjacent waypoint.
    for (flight, zone), windows in sorted(raw.items()):
        merged = []
        for start, end in sorted(windows):
            if merged and start <= merged[-1][1]:
                merged[-1] = merged[-1][0], max(end, merged[-1][1])
            else:
                merged.append((start, end))
        problem.passes.extend(Pass(flight, zone, start, end) for start, end in merged)

    def possible(a_start, a_end, b_start, b_end):
        slack = 2 * payload.max_shift_min * MINUTE + problem.time_sep
        return a_start - slack < b_end and b_start - slack < a_end

    by_zone = defaultdict(list)
    for passage in problem.passes:
        by_zone[passage.zone].append(passage)
    for zone_index, passes in by_zone.items():
        for index, a in enumerate(passes):
            for b in passes[index + 1:]:
                if a.flight != b.flight and possible(a.start, a.end, b.start, b.end):
                    problem.encounters.append(Encounter(a.flight, b.flight, a.start, a.end,
                                                        b.start, b.end, zones[zone_index].zone_id))
    # Nearby whole-segment intervals are conservative separation envelopes,
    # including crossings outside named zones. No rerouting is attempted.
    if payload.separation.lateral_km > 0:
        route_tree = STRtree([s.geometry for s in segments])
        for index, a in enumerate(segments):
            for other in route_tree.query(a.geometry, predicate="dwithin",
                                          distance=payload.separation.lateral_km * 1000):
                b = segments[other]
                if other > index and a.flight != b.flight and possible(a.start, a.end, b.start, b.end):
                    problem.encounters.append(Encounter(a.flight, b.flight, a.start, a.end,
                                                        b.start, b.end, "LATERAL"))
    if payload.include_civil:
        _civil_encounters(problem, scenario.adsb_tracks, segments, projection,
                          max(10, payload.separation.lateral_km) * 1000, ctx.scenario_time)
    return problem


def _civil_encounters(problem, tracks, segments, projection, radius_m, observed_at):
    """Predict latest observed heading/speed/climb in one-minute envelopes.

    Missing altitude requires time avoidance; missing velocity holds position.
    Exclude future observations and latest states that are on the ground.
    """
    latest = {}
    for track in tracks:
        if track.time <= observed_at and (
                track.icao24 not in latest or track.time > latest[track.icao24].time):
            latest[track.icao24] = track
    earliest = min(f.start - problem.max_shift * MINUTE for f in problem.flights)
    latest_time = max(f.end + problem.max_shift * MINUTE for f in problem.flights)
    route_tree = STRtree([s.geometry for s in segments])
    for icao, track in sorted(latest.items()):
        if track.on_ground:
            continue
        altitude = track.geo_alt_m if track.geo_alt_m is not None else track.alt_m
        step = max(earliest - problem.civil_time_sep, problem.ticks(track.time))
        finish = latest_time + problem.civil_time_sep

        def predict(tick):
            seconds = (tick - problem.ticks(track.time)) / 1000
            distance = (track.speed_ms or 0) * seconds if track.heading is not None else 0
            lon, lat, _ = GEOD.fwd(track.lon, track.lat, track.heading or 0, distance)
            point = projection.transform(lon, lat)
            height = None if altitude is None else (altitude + (track.vrate or 0) * seconds) / .3048
            return point, height

        while step < finish:
            end = min(step + MINUTE, finish)
            a, low = predict(step)
            b, high = predict(end)
            geometry = Point(a) if a == b else LineString([a, b])
            envelope = geometry.buffer(radius_m)
            for index in route_tree.query(envelope, predicate="intersects"):
                segment = segments[index]
                for start, stop in _intersection_times(segment, envelope):
                    slack = problem.max_shift * MINUTE + problem.civil_time_sep
                    if start - slack < end and step < stop + slack:
                        problem.civil.append(CivilEncounter(
                            segment.flight, f"CIVIL:{icao}", start, stop, step, end,
                            None if low is None else min(low, high),
                            None if high is None else max(low, high),
                        ))
            step = end
