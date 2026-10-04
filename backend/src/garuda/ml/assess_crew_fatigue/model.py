"""Approximate reservoir model; parameters match the synthetic roster defaults.

This is a demonstration model, not an aeromedical validation of fitness. Duty
observations are snapshots, not exact sleep records. Rest supplies up to 6.5 h
of sleep at the beginning of each 24 h rest cycle. No ML residual is required.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import math

from garuda.contracts.plan import SortieSlot

AWAKE_DRAIN = 0.02
SLEEP_RECOVERY = 0.10
CIRCADIAN = 0.05
PHI_HOUR = 17.0
TOT_PER_HOUR = 0.006
SLEEP_HOURS = 6.5
INERTIA = 0.03
INERTIA_DECAY_HOURS = 0.5
STEP_HOURS = 0.25


@dataclass
class State:
    reservoir: float = 0.8
    duty: float = 0.0
    rest: float = 0.0
    awake: float = 0.0
    on_duty: bool = False
    woke: bool = False
    asleep: bool = False


def circadian(at: datetime) -> float:
    # The roster generator uses Asia/Kolkata (UTC+05:30), independent of the
    # representation of the input timestamp. Bases have no timezone field.
    utc = at.astimezone(timezone.utc)
    hour = (utc.hour + utc.minute / 60 + utc.second / 3600 + 5.5) % 24
    return math.cos(2 * math.pi * (hour - PHI_HOUR) / 24)


def inertia(state: State) -> float:
    return INERTIA * math.exp(-state.awake / INERTIA_DECAY_HOURS) if state.woke and not state.asleep else 0.0


def score(state: State, at: datetime) -> float:
    effectiveness = (
        state.reservoir * (1 + CIRCADIAN * circadian(at))
        - TOT_PER_HOUR * state.duty - inertia(state)
    )
    # Stabilize exact public band boundaries against floating-point inversion
    # of an observed score (e.g. a stored 0.7 must remain AMBER).
    return round(min(1.0, max(0.0, 1 - effectiveness)), 12)


def step(state: State, hours: float, *, duty: bool, asleep: bool = False) -> None:
    """Fractional-hour equivalent of R += b * (1 - R) / R -= a."""
    if asleep:
        state.reservoir = 1 - (1 - state.reservoir) * (1 - SLEEP_RECOVERY) ** hours
        state.awake = 0.0
        state.woke = True
    else:
        state.reservoir = max(0.0, state.reservoir - AWAKE_DRAIN * hours)
        state.awake += hours
    state.duty = state.duty + hours if duty else 0.0
    state.rest = 0.0 if duty else state.rest + hours
    state.on_duty = duty
    state.asleep = asleep


def advance(
    state: State, start: datetime, end: datetime, *, duty: bool,
    peak_from: datetime | None = None,
) -> float:
    """Split at sleep edges and sample every 15 minutes, including exact ends."""
    peak = score(state, start) if peak_from is not None and start >= peak_from else 0.0
    moment = start
    while moment < end:
        remaining = (end - moment).total_seconds() / 3600
        hours = min(STEP_HOURS, remaining)
        asleep = False
        if not duty:
            if state.on_duty:
                state.rest = 0.0
            phase = round(state.rest % 24, 9) % 24
            asleep = phase < SLEEP_HOURS
            hours = min(hours, (SLEEP_HOURS if asleep else 24) - phase)
        if peak_from is not None and moment < peak_from < end:
            hours = min(hours, (peak_from - moment).total_seconds() / 3600)
        if state.asleep and not asleep:
            # A sortie can interrupt an inferred sleep opportunity. Include
            # the immediate waking penalty before it decays over the step.
            state.asleep = False
            if peak_from is not None and moment >= peak_from:
                peak = max(peak, score(state, moment))
        next_moment = min(end, moment + timedelta(hours=hours))
        # Datetimes resolve to microseconds; use the actual elapsed time.
        step(state, (next_moment - moment).total_seconds() / 3600, duty=duty, asleep=asleep)
        moment = next_moment
        if asleep and round(state.rest % 24, 9) == SLEEP_HOURS:
            state.asleep = False
        if peak_from is not None and moment >= peak_from:
            peak = max(peak, score(state, moment))
    return peak


def initial_state(duty: float, rest: float, stored_score: float, at: datetime) -> State:
    """Reconstruct one day, then preserve any worse fatigue already observed."""
    state = State()
    start = at - timedelta(hours=24)
    if duty > 0:
        # With no sleep history, allow one sleep opportunity before duty.
        sleep = min(SLEEP_HOURS, 24 - duty)
        step(state, sleep, duty=False, asleep=True)
        step(state, 24 - duty - sleep, duty=False)
        step(state, duty, duty=True)
    else:
        rest_in_day = min(rest, 24)
        step(state, 24 - rest_in_day, duty=False)
        state.rest = max(0.0, rest - 24)
        advance(state, start + timedelta(hours=24 - rest_in_day), at, duty=False)
    # Invert effectiveness to infer a reservoir from an observed score. Taking
    # the smaller reservoir avoids clearing a RED observation without rest.
    observed_reservoir = (
        1 - stored_score + TOT_PER_HOUR * state.duty + inertia(state)
    ) / (1 + CIRCADIAN * circadian(at))
    state.reservoir = min(state.reservoir, max(0.0, observed_reservoir))
    return state


def evaluate(
    state: State, observed_at: datetime, at: datetime, slots: list[SortieSlot],
) -> tuple[float, float, State, State]:
    baseline = replace(state)
    advance(baseline, observed_at, at, duty=baseline.on_duty)
    now = score(baseline, at)
    if not any(slot.end > at for slot in slots):
        return now, now, baseline, baseline
    projected = replace(state)
    peak = now
    moment = observed_at
    for slot in slots:
        if slot.end <= moment:
            continue
        start = max(moment, slot.start)
        peak = max(peak, advance(projected, moment, start, duty=projected.on_duty, peak_from=at))
        if slot.start < observed_at:
            projected.duty = max(projected.duty, (observed_at - slot.start).total_seconds() / 3600)
        peak = max(peak, advance(projected, start, slot.end, duty=True, peak_from=at))
        moment = slot.end
        # Planned end is the only explicit release time supplied by contracts.
        projected.on_duty = False
        projected.rest = 0.0
    return now, peak, baseline, projected
