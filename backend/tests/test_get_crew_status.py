"""Scenario-backed crew status and eligibility using the shared query rules."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from garuda.config import load_config
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.generators.missions import build_scenario
from garuda.schemas.crew import Crew, Currency
from garuda.tools.data.get_crew_status import GetCrewStatusInput, get_crew_status

WHEN = datetime(2026, 6, 1, 6, tzinfo=timezone.utc)
TICK = timedelta(microseconds=1)


def _crew(crew_id="CR-0001", **changes):
    values = dict(
        crew_id=crew_id, callsign="CS-0001", role="PILOT", sqn_id="SQ1", base_id="B1",
        primary_type="Su-30MKI", qualifications={"Su-30MKI": ["BVR", "AG"]},
        currency=Currency(by_type={"Su-30MKI": WHEN - timedelta(days=1)}),
        flight_hours_total=1000, flight_hours_last_30d=20,
        duty_hours_24h=0, rest_hours_since_last_duty=12, fatigue_score=0.3,
        medical_status="FIT", updated_at=WHEN,
    )
    values.update(changes)
    return Crew(**values)


def _ctx(crew, at=WHEN, snapshot=None):
    source = Mock()
    source.snapshot.return_value = snapshot if snapshot is not None else SimpleNamespace(crew=crew)
    source.base_ids.return_value = ["B1", "B2", "B3"]
    blank = SimpleNamespace()
    return ToolContext(scenario=source, plans=blank, models=blank, providers=blank, scenario_time=at)


def _run(ctx, **kwargs):
    return asyncio.run(get_crew_status(GetCrewStatusInput(**kwargs), ctx))


def test_reads_snapshot_preserves_unavailable_crew_and_orders_without_mutation():
    crew = [_crew("CR-0002", medical_status="GROUNDED"), _crew("CR-0001")]
    before = [member.model_dump() for member in crew]
    ctx = _ctx(crew)

    output = _run(ctx)

    assert output.model_dump() == {
        "total": 2,
        "items": [
            dict(crew_id="CR-0001", role="PILOT", base_id="B1", qualification="Su-30MKI",
                 fatigue_score=0.3, available=True),
            dict(crew_id="CR-0002", role="PILOT", base_id="B1", qualification="Su-30MKI",
                 fatigue_score=0.3, available=False),
        ],
    }
    ctx.scenario.snapshot.assert_called_once_with()
    assert [member.model_dump() for member in crew] == before


def test_filters_combine_and_max_fatigue_is_inclusive():
    crew = [
        _crew("CR-0001", base_id="B2", fatigue_score=0.4),
        _crew("CR-0002", role="WSO"),
        _crew("CR-0003", base_id="B3"),
        _crew("CR-0004", fatigue_score=0.4001),
        _crew("CR-0005", primary_type="Rafale", qualifications={"Rafale": ["BVR"]}),
        _crew("CR-0006", medical_status="LIMITED"),
    ]
    output = _run(_ctx(crew), base_ids=["B1", "B2", "B1"], role="PILOT",
                  qualification="Su-30MKI", max_fatigue=0.4)
    assert output.total == 2
    assert [(item.crew_id, item.available) for item in output.items] == [
        ("CR-0001", True), ("CR-0006", False),
    ]


@pytest.mark.parametrize("filters", [
    {"base_ids": []}, {"role": "unknown"}, {"qualification": "BVR"},
    {"qualification": "SU-30"}, {"qualification": ""}, {"max_fatigue": 0},
])
def test_filters_with_no_matches(filters):
    assert _run(_ctx([_crew()]), **filters).model_dump() == {"total": 0, "items": []}


def test_unknown_base_has_an_actionable_error():
    ctx = _ctx([_crew()])
    with pytest.raises(ToolInputError, match="unknown base_id B9; valid: B1, B2, B3"):
        _run(ctx, base_ids=["B9"])
    ctx.scenario.snapshot.assert_not_called()


def test_secondary_type_is_projected_and_its_own_currency_is_checked():
    member = _crew(
        qualifications={"Rafale": ["BVR", "AG"], "Su-30MKI": ["BVR", "AG"]},
        currency=Currency(by_type={"Su-30MKI": WHEN - timedelta(days=1)}),
    )
    unfiltered = _run(_ctx([member]))
    assert unfiltered.total == 1
    assert unfiltered.items[0].qualification == "Su-30MKI"
    assert unfiltered.items[0].available is True
    secondary = _run(_ctx([member]), qualification="Rafale")
    assert secondary.items[0].qualification == "Rafale"
    assert secondary.items[0].available is False
    current = member.model_copy(update={
        "currency": Currency(by_type={"Rafale": WHEN, "Su-30MKI": WHEN - timedelta(days=100)}),
    })
    assert _run(_ctx([current]), qualification="Rafale").items[0].available is True
    assert _run(_ctx([current])).items[0].available is False


@pytest.mark.parametrize(("moment", "available"), [
    (None, False), (WHEN - timedelta(days=90) - TICK, False),
    (WHEN - timedelta(days=90), True), (WHEN, True), (WHEN + TICK, False),
])
def test_qualification_currency_boundaries(moment, available):
    # A future last-flown date is legal in a later snapshot, but not current at WHEN.
    member = _crew(currency=Currency(by_type={} if moment is None else {"Su-30MKI": moment}),
                   updated_at=WHEN + timedelta(days=1))
    output = _run(_ctx([member]))
    assert output.total == 1
    assert output.items[0].available is available


@pytest.mark.parametrize(("changes", "available"), [
    ({"medical_status": "FIT"}, True),
    ({"medical_status": "LIMITED"}, False),
    ({"medical_status": "GROUNDED"}, False),
    ({"leave_until": WHEN + TICK}, False),
    ({"leave_until": WHEN}, True),
    ({"leave_until": WHEN - TICK}, True),
    ({"current_assignment": "M1"}, False),
    ({"duty_hours_24h": 1, "rest_hours_since_last_duty": 0}, False),
    ({"duty_hours_24h": 14, "rest_hours_since_last_duty": 0}, False),
    ({"duty_hours_24h": 15, "rest_hours_since_last_duty": 0}, False),
    ({"fatigue_score": 0.7}, True),
    ({"fatigue_score": 0.7001}, False),
    ({"rest_hours_since_last_duty": 12}, True),
])
def test_medical_leave_assignment_duty_rest_and_fatigue(changes, available):
    output = _run(_ctx([_crew(**changes)]))
    assert output.total == 1
    assert output.items[0].available is available


def test_shared_rest_blocker_is_used_for_an_unvalidated_snapshot_copy():
    # Normal Crew validation forbids this state; the query still guards against it.
    member = _crew().model_copy(update={"rest_hours_since_last_duty": 11.9})
    assert _run(_ctx([member])).items[0].available is False


def test_default_availability_uses_context_time():
    member = _crew(leave_until=WHEN + timedelta(hours=1))
    assert _run(_ctx([member])).items[0].available is False
    assert _run(_ctx([member], at=member.leave_until)).items[0].available is True


def test_generated_scenario_returns_entire_roster_including_unavailable_crew():
    config = load_config(Path(__file__).resolve().parents[2] / "config" / "scenario.yaml")
    scenario = build_scenario(config, seed=42)
    before = scenario.model_dump()
    ctx = _ctx(scenario.crew, at=scenario.crew[0].updated_at, snapshot=scenario)
    output = _run(ctx)
    assert output.total == len(scenario.crew) == 300
    assert [item.crew_id for item in output.items] == sorted(member.crew_id for member in scenario.crew)
    assert any(item.available for item in output.items)
    assert any(not item.available for item in output.items)
    assert scenario.model_dump() == before


@pytest.mark.parametrize("bounds", [
    {"available_from": WHEN}, {"available_to": WHEN},
    {"available_from": WHEN, "available_to": WHEN},
    {"available_from": WHEN, "available_to": WHEN + timedelta(hours=1)},
])
def test_explicit_availability_filters_unavailable_crew(bounds):
    crew = [
        _crew("CR-0001"),
        _crew("CR-0002", medical_status="LIMITED"),
        _crew("CR-0003", medical_status="GROUNDED"),
        _crew("CR-0004", leave_until=WHEN + timedelta(hours=1)),
        _crew("CR-0005", duty_hours_24h=14, rest_hours_since_last_duty=0),
        _crew("CR-0006", current_assignment="M1"),
        _crew("CR-0007", fatigue_score=0.7001),
        _crew("CR-0008").model_copy(update={"rest_hours_since_last_duty": 11.9}),
        _crew("CR-0009", currency=Currency(by_type={})),
    ]
    output = _run(_ctx(crew), **bounds)
    assert output.total == 1
    assert output.items[0].crew_id == "CR-0001"
    assert output.items[0].available is True


@pytest.mark.parametrize("bound", ["available_from", "available_to"])
def test_single_endpoint_is_an_instant_and_does_not_extend_to_scenario_time(bound):
    future = WHEN + timedelta(days=2)
    member = _crew(leave_until=future)
    output = _run(_ctx([member]), **{bound: future})
    assert output.total == 1
    assert output.items[0].available is True


@pytest.mark.parametrize(("leave_until", "total"), [
    (WHEN - TICK, 1), (WHEN, 1), (WHEN + TICK, 0),
    (WHEN + timedelta(hours=1), 0), (WHEN + timedelta(hours=2), 0),
])
def test_window_requires_leave_to_end_by_its_start(leave_until, total):
    output = _run(_ctx([_crew(leave_until=leave_until)]),
                  available_from=WHEN, available_to=WHEN + timedelta(hours=1))
    assert output.total == total


@pytest.mark.parametrize(("end_offset", "total"), [
    (-TICK, 1), (timedelta(0), 1), (TICK, 0),
])
def test_currency_expiration_at_closed_window_end(end_offset, total):
    # The crew is current at the start but expires during a longer query window.
    member = _crew(currency=Currency(by_type={"Su-30MKI": WHEN - timedelta(days=89)}))
    end = WHEN + timedelta(days=1)
    output = _run(_ctx([member]), available_from=WHEN, available_to=end + end_offset)
    assert output.total == total


def test_currency_that_becomes_current_inside_window_is_not_available_throughout():
    member = _crew(currency=Currency(by_type={"Su-30MKI": WHEN}), updated_at=WHEN)
    assert _run(_ctx([member]), available_from=WHEN - TICK, available_to=WHEN).total == 0


def test_window_checks_requested_secondary_type_and_combines_other_filters():
    member = _crew(qualifications={"Su-30MKI": ["BVR"], "Rafale": ["BVR"]},
                   currency=Currency(by_type={"Rafale": WHEN - timedelta(days=89)}))
    ctx = _ctx([member])
    filters = dict(base_ids=["B1"], role="PILOT", max_fatigue=0.3,
                   available_from=WHEN, available_to=WHEN + timedelta(days=1))
    assert _run(ctx, **filters).total == 0
    output = _run(ctx, qualification="Rafale", **filters)
    assert output.total == 1
    assert output.items[0].qualification == "Rafale"
    assert _run(ctx, qualification="Rafale", **{**filters, "max_fatigue": 0.2}).total == 0


def test_window_does_not_forecast_duty_release_fatigue_recovery_or_medical_change():
    crew = [
        _crew("CR-0001", duty_hours_24h=1, rest_hours_since_last_duty=0),
        _crew("CR-0002", fatigue_score=0.8),
        _crew("CR-0003", medical_status="LIMITED"),
        _crew("CR-0004", current_assignment="M1"),
    ]
    ctx = _ctx(crew)
    output = _run(ctx, available_from=WHEN + timedelta(days=2))
    assert output.model_dump() == {"total": 0, "items": []}


@pytest.mark.parametrize("bounds", [
    {"available_from": WHEN.replace(tzinfo=None)},
    {"available_to": WHEN.replace(tzinfo=None)},
    {"available_from": WHEN, "available_to": WHEN.replace(tzinfo=None)},
])
def test_naive_explicit_times_are_actionable_errors_even_for_empty_roster(bounds):
    ctx = _ctx([])
    with pytest.raises(ToolInputError, match="must be timezone-aware"):
        _run(ctx, **bounds)
    ctx.scenario.snapshot.assert_not_called()


def test_naive_default_time_is_an_actionable_error_even_for_empty_roster():
    with pytest.raises(ToolInputError, match="scenario_time must be timezone-aware"):
        _run(_ctx([], at=WHEN.replace(tzinfo=None)))


def test_reversed_window_is_an_actionable_error_even_for_empty_roster():
    ctx = _ctx([])
    with pytest.raises(ToolInputError, match="available_to must be at or after available_from"):
        _run(ctx, available_from=WHEN, available_to=WHEN - TICK)
    ctx.scenario.snapshot.assert_not_called()


def test_equivalent_offsets_and_equal_window_endpoints():
    local = WHEN.astimezone(timezone(timedelta(hours=5, minutes=30)))
    output = _run(_ctx([_crew(leave_until=WHEN)]), available_from=local, available_to=WHEN)
    assert output.total == 1
    assert output.items[0].available is True


def test_empty_roster_and_no_implicit_200_row_limit():
    assert _run(_ctx([])).model_dump() == {"total": 0, "items": []}
    crew = [_crew(f"CR-{i:04d}") for i in range(250)]
    assert _run(_ctx(crew), available_from=WHEN).total == 250
