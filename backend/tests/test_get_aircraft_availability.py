"""Aircraft data tool filtering, result shaping, and frozen contracts."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.schemas.aircraft import Aircraft
from garuda.tools.data.get_aircraft_availability import (
    SPEC,
    AircraftState,
    GetAircraftAvailabilityInput,
    GetAircraftAvailabilityOutput,
    get_aircraft_availability,
)

WHEN = datetime(2026, 6, 1, 6, tzinfo=timezone.utc)


def _aircraft(tail_no="SU-A0001", **changes):
    values = dict(
        tail_no=tail_no, type="Su-30MKI", sqn_id="SQ1", base_id="B1",
        status="FMC", fuel_kg=5200, fuel_capacity_kg=9000,
        flight_hours_total=1000, hours_to_next_inspection=37.5,
        inspection_cycle_hours=100, turnaround_min=45,
        capabilities=["BVR", "AG_PGM", "AAR_RECEIVER"],
        range_km=3000, cruise_kts=450, ceiling_ft=50000, updated_at=WHEN,
    )
    values.update(changes)
    if values["status"] in {"PMC", "NMC", "AOG"}:
        values.setdefault("status_reason", "radar_fault")
    if values["status"] in {"NMC", "AOG"}:
        values.setdefault("maint_return_eta", WHEN + timedelta(hours=18))
    return Aircraft(**values)


def _ctx(aircraft, probabilities=None):
    source = Mock()
    source.snapshot.return_value = SimpleNamespace(aircraft=aircraft)
    source.base_ids.return_value = ["B1", "B2", "B3"]
    availability = Mock()
    if probabilities is not None:
        availability.p_fmc.side_effect = lambda tail, at: probabilities[tail]
    blank = SimpleNamespace()
    return ToolContext(
        scenario=source, plans=blank, models=blank,
        providers=SimpleNamespace(availability=availability), scenario_time=WHEN,
    )


def _run(ctx, **kwargs):
    return asyncio.run(get_aircraft_availability(GetAircraftAvailabilityInput(**kwargs), ctx))


def test_reads_snapshot_and_preserves_all_statuses_and_state_values():
    fleet = [_aircraft(f"SU-A{i:04}", status=status) for i, status in enumerate(
        ["FMC", "PMC", "NMC", "AOG", "IN_MISSION"]
    )]
    ctx = _ctx(list(reversed(fleet)))
    output = _run(ctx)

    assert output.total == 5
    assert output.counts_by_status == {status: 1 for status in ["FMC", "PMC", "NMC", "AOG", "IN_MISSION"]}
    assert [item.model_dump() for item in output.items] == [
        {field: getattr(airframe, field) for field in AircraftState.model_fields}
        for airframe in fleet
    ]
    ctx.scenario.snapshot.assert_called_once_with()
    ctx.providers.availability.p_fmc.assert_not_called()


def test_plural_filters_and_all_capabilities_are_combined_before_limit():
    fleet = [
        _aircraft("SU-A0004", base_id="B2", type="Rafale"),
        _aircraft("SU-A0002", status="PMC"),
        _aircraft("SU-A0001"),
        _aircraft("SU-A0003", capabilities=["BVR"]),
        _aircraft("SU-A0005", base_id="B3"),
        _aircraft("SU-A0006", type="Tejas"),
        _aircraft("SU-A0007", status="NMC"),
    ]
    output = _run(
        _ctx(fleet), base_ids=["B1", "B2", "B1"], types=["Su-30MKI", "Rafale"],
        statuses=["FMC", "PMC"], capabilities=["BVR", "AG_PGM"], limit=2,
    )
    assert output.total == 3
    assert output.counts_by_status == {"FMC": 2, "PMC": 1}
    assert [item.tail_no for item in output.items] == ["SU-A0001", "SU-A0002"]


@pytest.mark.parametrize("filters", [
    {"base_ids": []}, {"types": []}, {"statuses": []},
    {"capabilities": ["UNKNOWN"]}, {"base_ids": ["B3"]},
])
def test_explicit_empty_filters_and_no_matches(filters):
    assert _run(_ctx([_aircraft()]), **filters).model_dump() == {
        "total": 0, "items": [], "counts_by_status": {},
    }


def test_empty_fleet_and_empty_capabilities():
    assert _run(_ctx([])).total == 0
    assert _run(_ctx([_aircraft()]), capabilities=[]).total == 1


def test_totals_cover_more_than_query_helpers_two_hundred_row_cap():
    output = _run(_ctx([_aircraft(f"SU-A{i:04}") for i in range(250)]), limit=200)
    assert output.total == 250
    assert len(output.items) == 200
    assert output.counts_by_status == {"FMC": 250}
    assert len(_run(_ctx([_aircraft(f"SU-A{i:04}") for i in range(70)])).items) == 50


@pytest.mark.parametrize("at", [WHEN, WHEN - timedelta(hours=1), WHEN.astimezone(timezone(timedelta(hours=5, minutes=30)))])
def test_current_or_past_time_uses_snapshot_without_forecast(at):
    ctx = _ctx([_aircraft()])
    assert _run(ctx, available_at=at).items[0].status == "FMC"
    ctx.providers.availability.p_fmc.assert_not_called()


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_limit_is_rejected_at_execution_without_changing_input_model(limit):
    ctx = _ctx([_aircraft()])
    with pytest.raises(ToolInputError, match="limit must be between 1 and 200"):
        _run(ctx, limit=limit)
    ctx.scenario.snapshot.assert_not_called()


@pytest.mark.parametrize("filters, message", [
    ({"base_ids": ["B9"]}, "unknown base_id B9; valid: B1, B2, B3"),
    ({"types": ["unknown"]}, "unknown aircraft type unknown"),
    ({"available_at": WHEN.replace(tzinfo=None)}, "available_at must be timezone-aware"),
])
def test_actionable_input_errors(filters, message):
    with pytest.raises(ToolInputError, match=message):
        _run(_ctx([_aircraft()]), **filters)


def test_type_aliases_follow_existing_aircraft_schema():
    output = _run(_ctx([_aircraft(type="AEW&C")]), types=["AWACS", "AEW"])
    assert output.total == 1
    assert output.items[0].type == "AEW&C"


def test_result_mutations_never_change_source_snapshot_or_other_calls():
    airframe = _aircraft()
    before = airframe.model_dump()
    ctx = _ctx([airframe])
    first = _run(ctx)
    first.items[0].capabilities.clear()
    first.items[0].fuel_kg = 0
    first.counts_by_status.clear()
    assert airframe.model_dump() == before
    assert _run(ctx).items[0].fuel_kg == 5200
    assert _run(ctx).items[0].capabilities == before["capabilities"]


def test_future_forecast_is_applied_before_status_filter_counts_and_limit():
    fleet = [
        _aircraft("SU-A0003", status="NMC"),
        _aircraft("SU-A0001"),
        _aircraft("SU-A0002", status="PMC"),
        _aircraft("SU-A0004", base_id="B2"),
    ]
    before = [airframe.model_dump() for airframe in fleet]
    ctx = _ctx(fleet, {"SU-A0001": 0.1, "SU-A0002": 0.7, "SU-A0003": 0.9})
    at = WHEN + timedelta(hours=2)
    output = _run(ctx, base_ids=["B1"], statuses=["FMC"], available_at=at, limit=1)

    assert output.total == 2
    assert output.counts_by_status == {"FMC": 2}
    assert [item.tail_no for item in output.items] == ["SU-A0002"]
    assert output.items[0].fuel_kg == fleet[2].fuel_kg
    assert [airframe.model_dump() for airframe in fleet] == before
    assert {call.args for call in ctx.providers.availability.p_fmc.call_args_list} == {
        (airframe.tail_no, at) for airframe in fleet if airframe.base_id == "B1"
    }


@pytest.mark.parametrize("current, probability, expected", [
    ("FMC", 0.0, "NMC"), ("FMC", 0.699, "NMC"),
    ("FMC", 0.5, "NMC"), ("NMC", 0.5, "NMC"),
    ("NMC", 0.7, "FMC"), ("NMC", 0.701, "FMC"),
    ("FMC", 0.7, "FMC"), ("AOG", 1.0, "FMC"),
    ("PMC", 0.1, "PMC"), ("NMC", 0.1, "NMC"),
    ("AOG", 0.1, "AOG"), ("IN_MISSION", 0.1, "IN_MISSION"),
])
def test_future_binary_status_mapping(current, probability, expected):
    ctx = _ctx([_aircraft(status=current)], {"SU-A0001": probability})
    output = _run(ctx, available_at=WHEN + timedelta(hours=1))
    assert output.items[0].status == expected
    assert output.counts_by_status == {expected: 1}


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("nan"), float("inf"), None, "0.5", True])
def test_future_invalid_probabilities_fail_clearly(probability):
    ctx = _ctx([_aircraft()], {"SU-A0001": probability})
    with pytest.raises(RuntimeError, match="invalid FMC probability for SU-A0001"):
        _run(ctx, available_at=WHEN + timedelta(hours=1))


def test_future_missing_provider_never_silently_returns_current_state():
    ctx = _ctx([_aircraft()])
    ctx.providers.availability.p_fmc.side_effect = NotImplementedError
    with pytest.raises(RuntimeError, match="forecast provider is not configured"):
        _run(ctx, available_at=WHEN + timedelta(hours=1))


def test_future_no_static_matches_does_not_call_provider():
    ctx = _ctx([_aircraft()])
    assert _run(ctx, base_ids=[], available_at=WHEN + timedelta(hours=1)).total == 0
    ctx.providers.availability.p_fmc.assert_not_called()


def test_public_schemas_and_spec_are_identical_to_frozen_contract():
    assert list(GetAircraftAvailabilityInput.model_fields) == [
        "base_ids", "types", "statuses", "capabilities", "available_at", "limit",
    ]
    assert GetAircraftAvailabilityInput().model_dump() == dict(
        base_ids=None, types=None, statuses=None, capabilities=None, available_at=None, limit=50,
    )
    schema = GetAircraftAvailabilityInput.model_json_schema()
    assert schema["properties"]["limit"] == {"default": 50, "maximum": 200, "title": "Limit", "type": "integer"}
    assert list(GetAircraftAvailabilityOutput.model_fields) == ["total", "items", "counts_by_status"]
    assert list(AircraftState.model_fields) == [
        "tail_no", "type", "base_id", "status", "fuel_kg", "hours_to_next_inspection", "capabilities",
    ]
    assert SPEC.name == "get_aircraft_availability"
    assert SPEC.input_model is GetAircraftAvailabilityInput
    assert SPEC.output_model is GetAircraftAvailabilityOutput
    assert SPEC.side_effects == "none"
    assert SPEC.timeout_s == 10
