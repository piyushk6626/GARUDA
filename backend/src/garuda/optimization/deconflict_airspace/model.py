"""Pure CP-SAT solve, with labelled assumptions for infeasibility diagnosis."""

from collections import defaultdict
from dataclasses import dataclass
from itertools import count
from math import ceil, floor
from time import monotonic

from garuda.optimization.deconflict_airspace.problem import MINUTE
from garuda.optimization.deconflict_airspace.schemas import Conflict


@dataclass(frozen=True)
class AirspaceSolution:
    status: str
    shifts: tuple[int, ...] = ()
    levels: tuple[int, ...] = ()
    conflicts: tuple[Conflict, ...] = ()

    @property
    def has_solution(self):
        return self.status in {"OPTIMAL", "FEASIBLE"}


def solve(problem, options=None) -> AirspaceSolution:
    if not problem.flights:
        return AirspaceSolution("OPTIMAL")
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:
        raise RuntimeError("deconflict_airspace requires the existing optimization extra: "
                           "pip install 'garuda[opt]'") from exc
    model = cp_model.CpModel()
    assumptions, reasons = [], {}
    predicate_ids, conjunction_ids = count(), count()

    def assumption(a, b, zone, detail):
        literal = model.NewBoolVar(f"rule_{len(assumptions)}")
        assumptions.append(literal)
        reasons[literal.Index()] = Conflict(flight_a=a, flight_b=b, zone=zone, detail=detail)
        model.AddAssumption(literal)
        return literal

    def predicate(left, right, relation="le"):
        literal = model.NewBoolVar(f"predicate_{next(predicate_ids)}")
        expressions = {"le": (left <= right, left > right),
                       "ge": (left >= right, left < right),
                       "lt": (left < right, left >= right),
                       "gt": (left > right, left <= right)}
        positive, negative = expressions[relation]
        model.Add(positive).OnlyEnforceIf(literal)
        model.Add(negative).OnlyEnforceIf(literal.Not())
        return literal

    def conjunction(literals):
        literal = model.NewBoolVar(f"and_{next(conjunction_ids)}")
        model.AddBoolAnd(literals).OnlyEnforceIf(literal)
        model.AddBoolOr([item.Not() for item in literals] + [literal])
        return literal

    shifts, levels, costs, altitude_costs = [], [], [], []
    for index, flight in enumerate(problem.flights):
        shift = model.NewIntVar(-problem.max_shift, problem.max_shift, f"shift_{index}")
        level = model.NewIntVar(0, flight.ceiling, f"level_{index}")
        window = assumption(flight.label, "", "WINDOW", "TOT window or departure before scenario_time")
        model.Add(shift >= flight.lower_shift).OnlyEnforceIf(window)
        model.Add(shift <= flight.upper_shift).OnlyEnforceIf(window)
        altitude = assumption(flight.label, "", "ALTITUDE", "Route altitude exceeds aircraft ceiling")
        model.Add(level >= flight.level).OnlyEnforceIf(altitude)
        absolute = model.NewIntVar(0, problem.max_shift, f"abs_shift_{index}")
        model.AddAbsEquality(absolute, shift)
        costs.append(flight.weight * absolute)
        altitude_costs.append(level - flight.level)
        shifts.append(shift)
        levels.append(level)

    members = defaultdict(list)
    tails = defaultdict(list)
    for index, flight in enumerate(problem.flights):
        members[flight.key[0]].append(index)
        tails[flight.key[1]].append(index)
    for indices in members.values():
        for index in indices[1:]:
            first = indices[0]
            rule = assumption(problem.flights[first].label, problem.flights[index].label,
                              "FORMATION", "Members of the same mission keep relative time offsets")
            model.Add(shifts[first] == shifts[index]).OnlyEnforceIf(rule)
    for indices in tails.values():
        for offset, a in enumerate(indices):
            for b in indices[offset + 1:]:
                fa, fb = problem.flights[a], problem.flights[b]
                rule = assumption(fa.label, fb.label, "AIRCRAFT", "One aircraft cannot fly overlapping sorties")
                model.AddBoolOr([
                    predicate(fa.end + shifts[a] * MINUTE, fb.start + shifts[b] * MINUTE),
                    predicate(fb.end + shifts[b] * MINUTE, fa.start + shifts[a] * MINUTE),
                ]).OnlyEnforceIf(rule)

    membership = {}
    for index, flight in enumerate(problem.flights):
        for zone_index in {p.zone for p in problem.passes if p.flight == index}:
            zone = problem.zones[zone_index]
            membership[index, zone_index] = conjunction([
                predicate(levels[index] * 100, zone.floor_ft, "ge"),
                predicate(levels[index] * 100, zone.ceiling_ft),
            ])
    times = [abs(t) for p in problem.passes for t in (p.start, p.end)]
    times.extend(abs(problem.ticks(t)) for z in problem.zones
                 for t in (z.active_from, z.active_to) if t is not None)
    bound = max(times, default=0) + problem.max_shift * MINUTE + 1
    intervals = defaultdict(list)
    capacities = {}
    for passage in problem.passes:
        index = passage.flight
        flight, zone = problem.flights[index], problem.zones[passage.zone]
        inside = membership[index, passage.zone]
        start, end = passage.start + shifts[index] * MINUTE, passage.end + shifts[index] * MINUTE
        active = []
        if zone.active_from is not None:
            begin, finish = problem.ticks(zone.active_from), problem.ticks(zone.active_to)
            active = [predicate(start, finish, "lt"), predicate(end, begin, "gt")]
        # Restricted/temporary reserved airspace is available only inside its
        # activation window. Prohibited/danger airspace is forbidden when active.
        if zone.type in {"RESTRICTED", "TRA"} and zone.active_from is not None:
            rule = assumption(flight.label, "", zone.zone_id, "Pass must fit the zone activation window")
            model.Add(start >= begin).OnlyEnforceIf([rule, inside])
            model.Add(end <= finish).OnlyEnforceIf([rule, inside])
        foreign = zone.mission_id is not None and zone.mission_id != flight.key[0]
        if zone.type in {"PROHIBITED", "DANGER"} or foreign:
            detail = "Zone is reserved for another mission" if foreign else "Forbidden airspace"
            rule = assumption(flight.label, "", zone.zone_id, detail + "; reroute with this zone blocked")
            model.AddBoolOr([inside.Not()] + [item.Not() for item in active]).OnlyEnforceIf(rule)
        if zone.capacity_per_hour is not None:
            if passage.zone not in capacities:
                occupants = sorted({p.flight for p in problem.passes if p.zone == passage.zone})
                labels = [problem.flights[i].label for i in occupants]
                capacities[passage.zone] = assumption(
                    labels[0], labels[1] if len(labels) > 1 else "", zone.zone_id,
                    f"Maximum simultaneous flight capacity exceeded; occupants: {', '.join(labels)}",
                )
            present = conjunction([inside, capacities[passage.zone]] + active)
            if active:
                clipped_start = model.NewIntVar(-bound, bound, "capacity_start")
                clipped_end = model.NewIntVar(-bound, bound, "capacity_end")
                duration = model.NewIntVar(0, 2 * bound, "capacity_duration")
                model.AddMaxEquality(clipped_start, [start, begin])
                model.AddMinEquality(clipped_end, [end, finish])
                # Inactive intervals need not satisfy start + size = end.
                model.Add(duration == clipped_end - clipped_start).OnlyEnforceIf(present)
                interval = model.NewOptionalIntervalVar(clipped_start, duration, clipped_end,
                                                        present, "capacity_pass")
            else:
                interval = model.NewOptionalIntervalVar(start, passage.end - passage.start,
                                                        end, present, "capacity_pass")
            intervals[passage.zone].append(interval)
    for zone_index, occupancy in intervals.items():
        model.AddCumulative(occupancy, [1] * len(occupancy), problem.zones[zone_index].capacity_per_hour)

    zone_indices = {z.zone_id: i for i, z in enumerate(problem.zones)}
    for encounter in problem.encounters:
        a, b = encounter.a, encounter.b
        rule = assumption(problem.flights[a].label, problem.flights[b].label,
                          encounter.zone, "Time or altitude separation required")
        choices = [
            predicate(encounter.end_a + shifts[a] * MINUTE + problem.time_sep,
                      encounter.start_b + shifts[b] * MINUTE),
            predicate(encounter.end_b + shifts[b] * MINUTE + problem.time_sep,
                      encounter.start_a + shifts[a] * MINUTE),
            predicate((levels[a] - levels[b]) * 100, problem.altitude_sep, "ge"),
            predicate((levels[b] - levels[a]) * 100, problem.altitude_sep, "ge"),
        ]
        if encounter.zone in zone_indices:
            zone_index = zone_indices[encounter.zone]
            choices.extend([membership[a, zone_index].Not(), membership[b, zone_index].Not()])
        model.AddBoolOr(choices).OnlyEnforceIf(rule)
    for encounter in problem.civil:
        index = encounter.flight
        rule = assumption(problem.flights[index].label, encounter.label, "CIVIL",
                          "Predicted civil traffic violates buffered separation")
        choices = [
            predicate(encounter.end + shifts[index] * MINUTE + problem.civil_time_sep,
                      encounter.civil_start),
            predicate(encounter.civil_end + problem.civil_time_sep,
                      encounter.start + shifts[index] * MINUTE),
        ]
        if encounter.low_ft is not None:
            choices.extend([
                predicate(levels[index] * 100, floor(encounter.low_ft - problem.civil_altitude_sep)),
                predicate(levels[index] * 100, ceil(encounter.high_ft + problem.civil_altitude_sep), "ge"),
            ])
        model.AddBoolOr(choices).OnlyEnforceIf(rule)
    # One weighted minute outranks all available altitude movement. Priority 1
    # is the highest priority. Altitude movement breaks equal time-cost ties.
    altitude_budget = sum(max(0, f.ceiling - f.level) for f in problem.flights)
    model.Minimize(sum(costs) * (altitude_budget + 1) + sum(altitude_costs))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = problem.seed % 2_147_483_647
    status = solver.Solve(model)
    if status in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        return AirspaceSolution(solver.StatusName(status), tuple(solver.Value(s) for s in shifts),
                                tuple(solver.Value(h) for h in levels))
    if status == cp_model.MODEL_INVALID:
        raise RuntimeError(f"invalid airspace CP-SAT model: {model.Validate()}")
    if status != cp_model.INFEASIBLE:
        return AirspaceSolution("UNKNOWN", conflicts=(Conflict(
            flight_a="", flight_b="", zone="SOLVER", detail="Search limit reached; draft was not changed",
        ),))
    # CP-SAT supplies a sufficient core. Shrink by deletion under a separate
    # short deadline; label it minimal only if every deletion was decided.
    core = list(solver.SufficientAssumptionsForInfeasibility())
    model.ClearObjective()
    deadline = monotonic() + 2
    minimal = True
    for literal in list(core):
        remaining = deadline - monotonic()
        if remaining <= 0:
            minimal = False
            break
        trial = [index for index in core if index != literal]
        model.ClearAssumptions()
        model.AddAssumptions([assumptions[index] for index in range(len(assumptions))
                              if assumptions[index].Index() in trial])
        solver.parameters.max_time_in_seconds = remaining
        trial_status = solver.Solve(model)
        if trial_status == cp_model.INFEASIBLE:
            core = trial
        elif trial_status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            minimal = False
    conflicts = [reasons[index].model_copy(update={
        "detail": ("Minimal conflict set: " if minimal else "Sufficient conflict set: ")
        + reasons[index].detail,
    }) for index in core]
    return AirspaceSolution("INFEASIBLE", conflicts=tuple(conflicts))
