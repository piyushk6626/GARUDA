"""Independent interval checker and unchanged public result shaping."""

from collections import defaultdict

from garuda.optimization.deconflict_airspace.problem import MINUTE
from garuda.optimization.deconflict_airspace.schemas import (
    Conflict, DeconflictAirspaceOutput, FlightAdjustment,
)


def check_conflicts(problem, shifts, levels) -> list[Conflict]:
    """Brute-force check; deliberately independent of CP-SAT literals."""
    conflicts = []

    def conflict(a, b, zone, detail):
        conflicts.append(Conflict(flight_a=a, flight_b=b, zone=zone, detail=detail))

    for index, flight in enumerate(problem.flights):
        if not flight.lower_shift <= shifts[index] <= flight.upper_shift:
            conflict(flight.label, "", "WINDOW", "TOT/departure window violated")
        if not flight.level <= levels[index] <= flight.ceiling:
            conflict(flight.label, "", "ALTITUDE", "Route altitude or aircraft ceiling violated")
    for a, fa in enumerate(problem.flights):
        for b in range(a + 1, len(problem.flights)):
            fb = problem.flights[b]
            if fa.key[0] == fb.key[0] and shifts[a] != shifts[b]:
                conflict(fa.label, fb.label, "FORMATION", "Relative mission offsets changed")
            if fa.key[1] == fb.key[1] and not (
                    fa.end + shifts[a] * MINUTE <= fb.start + shifts[b] * MINUTE
                    or fb.end + shifts[b] * MINUTE <= fa.start + shifts[a] * MINUTE):
                conflict(fa.label, fb.label, "AIRCRAFT", "Aircraft sorties overlap")
    occupancy = defaultdict(list)
    for passage in problem.passes:
        index, zone = passage.flight, problem.zones[passage.zone]
        altitude = levels[index] * 100
        if not zone.floor_ft <= altitude <= zone.ceiling_ft:
            continue
        start, end = passage.start + shifts[index] * MINUTE, passage.end + shifts[index] * MINUTE
        begin = problem.ticks(zone.active_from) if zone.active_from is not None else start
        finish = problem.ticks(zone.active_to) if zone.active_to is not None else end
        if (zone.type in {"RESTRICTED", "TRA"} and zone.active_from is not None
                and not (begin <= start and end <= finish)):
            conflict(problem.flights[index].label, "", zone.zone_id, "Pass outside activation window")
        foreign = zone.mission_id is not None and zone.mission_id != problem.flights[index].key[0]
        if max(start, begin) < min(end, finish):
            if zone.type in {"PROHIBITED", "DANGER"} or foreign:
                conflict(problem.flights[index].label, "", zone.zone_id,
                         "Forbidden/reserved airspace; reroute with this zone blocked")
            if zone.capacity_per_hour is not None:
                occupancy[passage.zone].append((max(start, begin), min(end, finish), index))
    for zone_index, passes in occupancy.items():
        # Departure events sort before arrivals: occupancy is half-open.
        events = sorted([(start, 1, index) for start, _, index in passes]
                        + [(end, -1, index) for _, end, index in passes])
        active = set()
        for _, change, index in events:
            if change < 0:
                active.discard(index)
            else:
                active.add(index)
                if len(active) > problem.zones[zone_index].capacity_per_hour:
                    labels = [problem.flights[i].label for i in sorted(active)]
                    conflict(labels[0], labels[1] if len(labels) > 1 else "",
                             problem.zones[zone_index].zone_id,
                             f"Maximum simultaneous capacity exceeded: {', '.join(labels)}")
    zones = {zone.zone_id: zone for zone in problem.zones}
    for encounter in problem.encounters:
        a, b = encounter.a, encounter.b
        if encounter.zone in zones:
            zone = zones[encounter.zone]
            if not all(zone.floor_ft <= levels[i] * 100 <= zone.ceiling_ft for i in (a, b)):
                continue
        if (encounter.end_a + shifts[a] * MINUTE + problem.time_sep
                <= encounter.start_b + shifts[b] * MINUTE
                or encounter.end_b + shifts[b] * MINUTE + problem.time_sep
                <= encounter.start_a + shifts[a] * MINUTE
                or abs(levels[a] - levels[b]) * 100 >= problem.altitude_sep):
            continue
        conflict(problem.flights[a].label, problem.flights[b].label, encounter.zone,
                 "Time and altitude separation violated")
    for encounter in problem.civil:
        index, altitude = encounter.flight, levels[encounter.flight] * 100
        if (encounter.end + shifts[index] * MINUTE + problem.civil_time_sep <= encounter.civil_start
                or encounter.civil_end + problem.civil_time_sep <= encounter.start + shifts[index] * MINUTE
                or (encounter.low_ft is not None and (
                    altitude <= encounter.low_ft - problem.civil_altitude_sep
                    or altitude >= encounter.high_ft + problem.civil_altitude_sep))):
            continue
        conflict(problem.flights[index].label, encounter.label, "CIVIL",
                 "Predicted civil traffic violates buffered separation")
    return list({(c.flight_a, c.flight_b, c.zone, c.detail): c for c in conflicts}.values())


def explain(problem, solution) -> DeconflictAirspaceOutput:
    if not solution.has_solution:
        conflicts = list(solution.conflicts)
        if not conflicts:
            conflicts = check_conflicts(problem, [0] * len(problem.flights),
                                        [f.level for f in problem.flights])
        return DeconflictAirspaceOutput(status=solution.status, adjusted_flights=[],
                                       remaining_conflicts=conflicts, total_shift_min=0)
    conflicts = check_conflicts(problem, solution.shifts, solution.levels)
    if conflicts:
        raise RuntimeError(f"airspace solver result failed independent validation: {conflicts}")
    adjustments = []
    for index, flight in enumerate(problem.flights):
        altitude = solution.levels[index] * 100
        changed_altitude = any(w.alt_ft != altitude for leg_index in flight.leg_indices
                               for w in problem.plan.legs[leg_index].waypoints)
        if solution.shifts[index] or changed_altitude:
            adjustments.append(FlightAdjustment(flight=flight.label, shift_min=solution.shifts[index],
                                                altitude_block=f"FL{solution.levels[index]:03d}"))
    return DeconflictAirspaceOutput(status=solution.status, adjusted_flights=adjustments,
                                   remaining_conflicts=[],
                                   total_shift_min=sum(abs(s) for s in solution.shifts))
