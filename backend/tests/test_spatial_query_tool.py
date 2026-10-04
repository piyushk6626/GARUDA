"""spatial_query tool: scenario geography in, geometry op out."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from garuda.bases import BASES
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.generators.gis import great_circle_km
from garuda.schemas.gis import BaseRecord, GeoFeature
from garuda.schemas.mission import Target
from garuda.tools.data.spatial_query import GeoRef, SpatialQueryInput, spatial_query

WHEN = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)


def _base(airfield) -> BaseRecord:
    return BaseRecord(
        base_id=airfield.base_id,
        name=airfield.name,
        icao=airfield.icao,
        lat=airfield.lat,
        lon=airfield.lon,
        elevation_ft=airfield.elevation_ft,
        runway_count=1,
        runway_length_m=2000,
        shelter_count=8,
        fuel_capacity_l=1000,
        ground_crew_teams=1,
        max_sorties_per_hour=1.0,
    )


def _world() -> SimpleNamespace:
    jodhpur = BASES[0]
    return SimpleNamespace(
        bases=[_base(airfield) for airfield in BASES[:2]],
        features=[
            GeoFeature(
                feature_id="city:jodhpur",
                kind="city",
                name="Jodhpur",
                wkt=f"POINT({jodhpur.lon} {jodhpur.lat + 0.01})",
            ),
            GeoFeature(
                feature_id="river:luni",
                kind="river",
                name="Luni",
                wkt="LINESTRING(74.0 26.0, 75.0 27.0)",
            ),
        ],
        borders=[],
        targets=[
            Target(
                target_id="T-7",
                target_class="BRIDGE",
                lat=27.0,
                lon=74.0,
                value=1.0,
                hardness=0.2,
                defended_by=[],
            )
        ],
    )


def _ctx() -> ToolContext:
    blank = SimpleNamespace()
    return ToolContext(
        scenario=SimpleNamespace(snapshot=lambda: _world()),
        plans=blank,
        models=blank,
        providers=blank,
        scenario_time=WHEN,
    )


def _run(payload: SpatialQueryInput):
    return asyncio.run(spatial_query(payload, _ctx()))


def test_distance_km_is_the_great_circle_between_two_bases():
    b1, b2 = BASES[0], BASES[1]

    output = _run(
        SpatialQueryInput(op="distance_km", a=GeoRef(entity_id="B1"), b=GeoRef(entity_id="B2"))
    )

    assert output.op == "distance_km"
    assert output.value_km == round(great_circle_km(b1.lat, b1.lon, b2.lat, b2.lon), 3)
    assert output.hits == []


def test_within_radius_lists_places_inside_and_drops_the_far_base():
    output = _run(SpatialQueryInput(op="within_radius", a=GeoRef(entity_id="B1"), radius_km=20))

    assert output.value_km is None
    assert "B1" in output.hits
    assert "city:jodhpur" in output.hits
    assert "B2" not in output.hits


def test_nearest_returns_the_closest_place_and_its_distance():
    output = _run(SpatialQueryInput(op="nearest", a=GeoRef(entity_id="B1")))

    assert output.hits == ["city:jodhpur"]
    assert output.value_km is not None
    assert output.value_km < 5


def test_intersects_reports_the_geometry_that_crosses():
    output = _run(
        SpatialQueryInput(
            op="intersects",
            a=GeoRef(lat=26.5, lon=74.5),
            b=GeoRef(entity_id="river:luni"),
        )
    )

    assert output.hits == ["river:luni"]
    assert output.value_km is not None
    assert output.value_km < 1


def test_buffer_includes_places_inside_the_circle_except_the_origin():
    output = _run(SpatialQueryInput(op="buffer", a=GeoRef(entity_id="B1"), radius_km=15))

    assert "city:jodhpur" in output.hits
    assert "B1" not in output.hits
    assert "B2" not in output.hits


def test_missing_second_place_is_an_actionable_error():
    with pytest.raises(ToolInputError, match="distance_km needs a second geometry"):
        _run(SpatialQueryInput(op="distance_km", a=GeoRef(entity_id="B1")))


def test_unknown_base_names_the_valid_ids():
    with pytest.raises(ToolInputError, match="unknown base_id B9"):
        _run(SpatialQueryInput(op="nearest", a=GeoRef(entity_id="B9")))
