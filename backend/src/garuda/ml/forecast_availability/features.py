"""Features available through the existing scenario and plan contracts.

Future history is excluded. Ground-crew load, spare stock and surge are not
present in GeneratedScenario; missing values stay explicit. Training must use
the same columns and missing-value policy.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from garuda.contracts.plan import PlanDraft
from garuda.contracts.tool import ToolInputError
from garuda.schemas.aircraft import Aircraft, AircraftHistory
from garuda.schemas.mission import GeneratedScenario


FEATURE_NAMES = (
    "type",
    "base",
    "flight_hours_total",
    "hours_to_next_inspection",
    "hours_since_inspection",
    "current_status",
    "time_in_status_h",
    "maintenance_eta_h",
    "sorties_last_24h",
    "sorties_last_72h",
    "recent_failure_count",
    "planned_sorties",
    "planned_flight_hours",
    "ground_crew_load",
    "spare_part_available",
    "surge_indicator",
    "hour_of_day",
)


@dataclass(frozen=True)
class PlannedSortie:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class AircraftFeatures:
    aircraft: Aircraft
    values: dict[str, object]
    sorties: tuple[PlannedSortie, ...]


def require_aware(moment: datetime, field: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ToolInputError(f"{field} must be timezone-aware")


def planned_sorties(
    scenario: GeneratedScenario,
    plan: PlanDraft | None,
) -> dict[str, tuple[PlannedSortie, ...]]:
    """Prefer timed slots; use mission duration for assignment-only plans.

    Crew-role slots for the same tail/mission/departure count once, as does
    their corresponding Assignment.
    """
    if plan is None:
        return {}
    tails = {aircraft.tail_no for aircraft in scenario.aircraft}
    missions = {mission.mission_id: mission for mission in scenario.missions}
    intervals: dict[tuple[str, str, datetime], PlannedSortie] = {}
    for slot in plan.sortie_slots:
        require_aware(slot.start, "sortie slot start")
        require_aware(slot.end, "sortie slot end")
        if slot.tail_no not in tails:
            raise ToolInputError(f"plan references unknown aircraft {slot.tail_no}")
        if slot.end <= slot.start:
            raise ToolInputError(f"sortie slot {slot.slot_id} must end after its start")
        key = (slot.tail_no, slot.mission_id, slot.start)
        previous = intervals.get(key)
        intervals[key] = PlannedSortie(
            slot.start,
            max(slot.end, previous.end) if previous else slot.end,
        )
    for assignment in plan.assignments:
        require_aware(assignment.depart, "assignment departure")
        if assignment.tail_no not in tails:
            raise ToolInputError(
                f"plan references unknown aircraft {assignment.tail_no}"
            )
        key = (assignment.tail_no, assignment.mission_id, assignment.depart)
        if key in intervals:
            continue
        mission = missions.get(assignment.mission_id)
        if mission is None:
            raise ToolInputError(
                f"mission {assignment.mission_id} needs a timed sortie slot or a scenario mission",
            )
        intervals[key] = PlannedSortie(
            assignment.depart,
            assignment.depart + timedelta(minutes=mission.duration_min),
        )
    by_tail: dict[str, list[PlannedSortie]] = defaultdict(list)
    for (tail, _, _), sortie in intervals.items():
        by_tail[tail].append(sortie)
    return {
        tail: tuple(sorted(items, key=lambda item: (item.start, item.end)))
        for tail, items in by_tail.items()
    }


def features(
    scenario: GeneratedScenario,
    aircraft: list[Aircraft],
    at: datetime,
    plan: PlanDraft | None = None,
) -> list[AircraftFeatures]:
    require_aware(at, "scenario_time")
    history: dict[str, list[AircraftHistory]] = defaultdict(list)
    for row in scenario.aircraft_history:
        require_aware(row.time, "aircraft history time")
        if row.time <= at:
            history[row.tail_no].append(row)
    by_tail = planned_sorties(scenario, plan)
    result = []
    for airframe in aircraft:
        if airframe.updated_at > at:
            raise ToolInputError(
                f"aircraft {airframe.tail_no} snapshot is later than scenario_time"
            )
        rows = sorted(history[airframe.tail_no], key=lambda row: row.time)
        status_since = airframe.updated_at
        for row in reversed(rows):
            if row.status != airframe.status:
                break
            status_since = min(status_since, row.time)
        sorties_24 = sorties_72 = failures = 0
        previous = None
        for row in rows:
            if previous is not None and row.status != previous.status:
                if at - timedelta(hours=72) < row.time <= at:
                    if row.status == "IN_MISSION":
                        sorties_72 += 1
                        if row.time > at - timedelta(hours=24):
                            sorties_24 += 1
                    if row.status in {"PMC", "NMC", "AOG"} and previous.status in {
                        "FMC",
                        "IN_MISSION",
                    }:
                        failures += 1
            previous = row
        eta = airframe.maint_return_eta
        values: dict[str, object] = {
            "type": airframe.type,
            "base": airframe.base_id,
            "flight_hours_total": airframe.flight_hours_total,
            "hours_to_next_inspection": airframe.hours_to_next_inspection,
            "hours_since_inspection": airframe.inspection_cycle_hours
            - airframe.hours_to_next_inspection,
            "current_status": airframe.status,
            "time_in_status_h": (at - status_since).total_seconds() / 3600,
            "maintenance_eta_h": (
                max(0.0, (eta - at).total_seconds() / 3600) if eta else None
            ),
            "sorties_last_24h": sorties_24,
            "sorties_last_72h": sorties_72,
            "recent_failure_count": failures,
            "planned_sorties": 0,
            "planned_flight_hours": 0.0,
            "ground_crew_load": None,
            "spare_part_available": (
                False if airframe.status_reason == "awaiting_parts" else None
            ),
            "surge_indicator": None,
            "hour_of_day": at.hour,
        }
        result.append(
            AircraftFeatures(airframe, values, by_tail.get(airframe.tail_no, ()))
        )
    return result


def horizon_features(
    row: AircraftFeatures, at: datetime, horizon: int
) -> dict[str, object]:
    target = at + timedelta(hours=horizon)
    values = dict(row.values)
    values["planned_sorties"] = sum(
        at <= sortie.start < target for sortie in row.sorties
    )
    values["planned_flight_hours"] = sum(
        max(
            0.0,
            (min(sortie.end, target) - max(sortie.start, at)).total_seconds() / 3600,
        )
        for sortie in row.sorties
    )
    return values


def blocking_reason(row: AircraftFeatures, at: datetime, horizon: int) -> str | None:
    """Known maintenance and timed plan commitments override model output."""
    target = at + timedelta(hours=horizon)
    airframe = row.aircraft
    if airframe.maint_return_eta and target < airframe.maint_return_eta:
        return f"Maintenance continues until {airframe.maint_return_eta.isoformat()}"
    for sortie in row.sorties:
        ready_at = sortie.end + timedelta(minutes=airframe.turnaround_min)
        if sortie.start <= target < ready_at:
            return "Planned sortie or turnaround covers the forecast time"
    margin = airframe.hours_to_next_inspection
    if (
        airframe.status_reason == "scheduled_inspection"
        and airframe.maint_return_eta
        and target >= airframe.maint_return_eta
    ):
        margin = airframe.inspection_cycle_hours
        start = max(at, airframe.maint_return_eta)
    else:
        start = at
    planned_hours = sum(
        max(
            0.0,
            (min(sortie.end, target) - max(sortie.start, start)).total_seconds() / 3600,
        )
        for sortie in row.sorties
    )
    if planned_hours >= margin:
        return "Inspection is due by the forecast time"
    return None
