"""Scenario-backed airspace queries: geometry, inclusive altitude, half-open time."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import Polygon, box

from garuda.config import load_config
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.generators.missions import build_scenario
from garuda.queries.airspace import zones_intersecting
from garuda.schemas.airspace import AirspaceZone
from garuda.tools.data.get_airspace import GetAirspaceInput, get_airspace

WHEN = datetime(2026, 6, 1, 6, tzinfo=timezone.utc)
FOOTPRINT = box(77, 28, 78, 29)
ROUTE = [(28.5, 76.5), (28.5, 78.5)]
AREA = [(28.2, 77.2), (28.2, 77.8), (28.8, 77.8), (28.8, 77.2)]


def _zone(zone_id="Z1", **overrides):
    fields = dict(
        zone_id=zone_id,
        name=f"Zone {zone_id}",
        type="CTR",
        wkt=FOOTPRINT.wkt,
        floor_ft=1_000,
        ceiling_ft=10_000,
        controlling_authority="IAF",
        source="synthetic",
    )
    fields.update(overrides)
    return AirspaceZone(**fields)


class ScenarioSource:
    def __init__(self, zones):
        self.scenario = SimpleNamespace(zones=zones)

    def snapshot(self):
        return self.scenario


def _ctx(zones, at=WHEN):
    unused = SimpleNamespace()
    return ToolContext(
        scenario=ScenarioSource(zones),
        plans=unused,
        models=unused,
        providers=unused,
        scenario_time=at,
    )


def _run(zones, **payload):
    return asyncio.run(get_airspace(GetAirspaceInput(**payload), _ctx(zones)))


def _ids(output):
    assert output.total == len(output.items)
    return [item.zone_id for item in output.items]


def test_route_crosses_zone_between_endpoints_and_uses_longitude_as_x():
    transposed = _zone("SWAPPED", wkt=box(28, 77, 29, 78).wkt)
    output = _run([transposed, _zone()], route=ROUTE)
    assert _ids(output) == ["Z1"]
    assert output.items[0].model_dump() == dict(
        zone_id="Z1", name="Zone Z1", type="CTR", floor_ft=1_000, ceiling_ft=10_000
    )


@pytest.mark.parametrize(
    ("geometry", "expected"),
    [
        ({"route": [(28, 76), (28, 77)]}, ["Z1"]),  # touches a corner
        ({"polygon": [(29, 78), (29, 79), (30, 79), (30, 78)]}, ["Z1"]),
        ({"polygon": AREA}, ["Z1"]),  # contained polygon
        ({"polygon": [*AREA, AREA[0]]}, ["Z1"]),  # already closed ring
        ({"polygon": [(27, 76), (27, 79), (30, 79), (30, 76)]}, ["Z1"]),
        ({"polygon": [(28.5, 77.5), (28.5, 78.5), (29.5, 78.5), (29.5, 77.5)]}, ["Z1"]),
        ({"route": [(30, 77), (30, 78)]}, []),
        ({"polygon": [(30, 77), (30, 78), (31, 78), (31, 77)]}, []),
    ],
)
def test_route_and_polygon_intersection_includes_boundary_contacts(geometry, expected):
    assert _ids(_run([_zone()], **geometry)) == expected


@pytest.mark.parametrize("geometry", [{"route": [(28.5, 77.4), (28.5, 77.6)]}, {"polygon": AREA}])
def test_geometry_inside_zone_hole_does_not_intersect(geometry):
    footprint = Polygon(FOOTPRINT.exterior.coords, holes=[box(77.1, 28.1, 77.9, 28.9).exterior.coords])
    assert _ids(_run([_zone(wkt=footprint.wkt)], **geometry)) == []


def test_overlapping_bounding_boxes_are_not_enough():
    triangle = Polygon([(77, 28), (78, 28), (77, 29)])
    area = [(28.8, 77.8), (28.8, 77.9), (28.9, 77.9), (28.9, 77.8)]
    assert _ids(_run([_zone(wkt=triangle.wkt)], polygon=area)) == []


def test_route_follows_segments_while_polygon_includes_its_interior():
    perimeter = [(27, 76), (27, 79), (30, 79), (30, 76), (27, 76)]
    assert _ids(_run([_zone()], route=perimeter)) == []
    assert _ids(_run([_zone()], polygon=perimeter)) == ["Z1"]


@pytest.mark.parametrize("spatial", [{}, {"route": ROUTE}, {"polygon": AREA}])
@pytest.mark.parametrize(("altitude", "expected"), [(999, []), (1_000, ["Z1"]), (10_000, ["Z1"]), (10_001, []), (0, []), (None, ["Z1"])])
def test_altitude_bounds_are_inclusive_and_none_omits_filter(spatial, altitude, expected):
    assert _ids(_run([_zone()], altitude_ft=altitude, **spatial)) == expected


@pytest.mark.parametrize("spatial", [{}, {"route": ROUTE}, {"polygon": AREA}])
@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (WHEN - timedelta(microseconds=1), ["PERMANENT"]),
        (WHEN, ["PERMANENT", "SCHEDULED"]),
        (WHEN + timedelta(hours=1) - timedelta(microseconds=1), ["PERMANENT", "SCHEDULED"]),
        (WHEN + timedelta(hours=1), ["PERMANENT"]),
        (WHEN + timedelta(hours=2), ["PERMANENT"]),
        (WHEN.astimezone(timezone(timedelta(hours=5, minutes=30))), ["PERMANENT", "SCHEDULED"]),
    ],
)
def test_activation_is_half_open_and_permanent_zones_remain_active(spatial, at, expected):
    zones = [
        _zone("SCHEDULED", type="TRA", active_from=WHEN, active_to=WHEN + timedelta(hours=1)),
        _zone("PERMANENT"),
    ]
    assert _ids(_run(zones, time=at, **spatial)) == expected


@pytest.mark.parametrize("time_fields", [{}, {"time": None}])
def test_omitted_time_uses_scenario_clock(time_fields):
    zone = _zone(active_from=WHEN, active_to=WHEN + timedelta(hours=1))
    ctx = _ctx([zone], WHEN + timedelta(hours=1))
    output = asyncio.run(get_airspace(GetAirspaceInput(**time_fields), ctx))
    assert _ids(output) == []
    assert _ids(_run([zone], **time_fields)) == ["Z1"]


def test_no_spatial_filter_returns_remote_zones_and_preserves_types():
    types = ["PROHIBITED", "RESTRICTED", "DANGER", "CTR", "TMA", "ADIZ", "CAP", "AAR_TRACK", "CORRIDOR", "TRA"]
    zones = [_zone(f"Z{i:02}", type=kind, wkt=box(i, 0, i + 1, 1).wkt) for i, kind in enumerate(types)]
    output = _run(zones, route=None, polygon=None, altitude_ft=None, time=None)
    assert [item.type for item in output.items] == types
    assert output.total == len(types)


def test_results_are_sorted_repeatable_and_do_not_mutate_scenario():
    zones = [_zone("Z2"), _zone("Z1")]
    before = [zone.model_dump() for zone in zones]
    first = _run(zones, route=ROUTE)
    second = _run(list(reversed(zones)), route=ROUTE)
    assert _ids(first) == ["Z1", "Z2"]
    assert first == second
    first.items[0].name = "changed result"
    assert [zone.model_dump() for zone in zones] == before


@pytest.mark.parametrize("payload", [{}, {"route": ROUTE}, {"polygon": AREA}])
def test_empty_scenario_returns_empty_output(payload):
    assert _ids(_run([], **payload)) == []


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"route": ROUTE, "polygon": AREA}, "not both"),
        ({"route": [], "polygon": []}, "not both"),
        ({"route": []}, "at least 2"),
        ({"route": [(28, 77)]}, "at least 2"),
        ({"polygon": []}, "at least 3"),
        ({"polygon": [(28, 77), (29, 78)]}, "at least 3"),
        ({"polygon": [(28, 77), (29, 78), (28, 78), (29, 77)]}, "valid"),
        ({"polygon": [(28, 77), (28, 78), (28, 79)]}, "valid"),
        ({"route": [(28, 77), (28, 77)]}, "valid"),
        ({"route": [(float("nan"), 77), (28, 78)]}, "finite"),
        ({"polygon": [(28, 77), (28, float("inf")), (29, 78)]}, "finite"),
        ({"route": [(91, 77), (28, 78)]}, "within bounds"),
        ({"polygon": [(28, 181), (28, 78), (29, 78)]}, "within bounds"),
        ({"time": WHEN.replace(tzinfo=None)}, "timezone-aware"),
    ],
)
def test_invalid_inputs_raise_actionable_errors_even_without_zones(payload, message):
    with pytest.raises(ToolInputError, match=message):
        _run([], **payload)


def test_naive_scenario_time_is_rejected():
    with pytest.raises(ToolInputError, match="timezone-aware"):
        asyncio.run(get_airspace(GetAirspaceInput(), _ctx([], WHEN.replace(tzinfo=None))))


def test_query_rejects_naive_time_even_for_empty_zone_list():
    with pytest.raises(ValueError, match="timezone-aware"):
        zones_intersecting([], None, None, WHEN.replace(tzinfo=None))


def test_tool_reads_generated_scenario_zones():
    config_path = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"
    scenario = build_scenario(load_config(config_path), 42)
    at = scenario.aircraft[0].updated_at
    output = asyncio.run(get_airspace(GetAirspaceInput(), _ctx(scenario.zones, at)))
    expected = sorted(zone.zone_id for zone in scenario.zones if zone.active_from is None)
    assert _ids(output) == expected
    assert output.total > 1
