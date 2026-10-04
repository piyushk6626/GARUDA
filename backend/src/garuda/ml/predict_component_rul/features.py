"""Adapt existing inspection records without changing scenario schemas."""

from dataclasses import dataclass
import math

from garuda.contracts.tool import ToolInputError


@dataclass(frozen=True)
class ComponentFeatures:
    tail: str
    component: str
    age_hours: float
    inspection_hours_remaining: float


def features(
    snapshot, tail_nos: list[str] | None, components: list[str]
) -> list[ComponentFeatures]:
    rows = []
    if not components:
        return rows
    for aircraft in sorted(snapshot.aircraft, key=lambda item: item.tail_no):
        if tail_nos is not None and aircraft.tail_no not in tail_nos:
            continue
        cycle = aircraft.inspection_cycle_hours
        remaining = aircraft.hours_to_next_inspection
        if (
            not math.isfinite(cycle)
            or not math.isfinite(remaining)
            or not 0 <= remaining <= cycle
            or cycle <= 0
        ):
            raise ToolInputError(
                f"invalid inspection hours for aircraft {aircraft.tail_no}"
            )
        # No component log exists in the frozen scenario: all components share
        # this age proxy. It is not a measured overhaul age.
        age = cycle - remaining
        rows.extend(
            ComponentFeatures(aircraft.tail_no, component, age, remaining)
            for component in components
        )
    return rows
