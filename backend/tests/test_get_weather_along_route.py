import asyncio
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from garuda.contracts.tool import ToolContext
from garuda.queries.weather import get_weather_along_route as query_weather_along_route
from garuda.schemas.weather import WeatherCell, WeatherRouteQuery, WeatherRouteResult, WeatherSegment
from garuda.tools.data.get_weather_along_route import (
    SPEC,
    GetWeatherAlongRouteInput,
    GetWeatherAlongRouteOutput,
    get_weather_along_route,
)

T0 = datetime(2026, 6, 1, 10, tzinfo=timezone.utc)


def cell(lat=26.0, lon=72.0, time=T0, **changes):
    fields = dict(
        time=time, lat=lat, lon=lon, cloud_low_pct=10, cloud_mid_pct=0,
        cloud_high_pct=0, ceiling_ft=None, visibility_km=12, wind_kts=9,
        wind_dir_deg=230, gust_kts=12, precip_mm_h=0, cape_jkg=0,
        temp_c=20, freezing_level_ft=10_000, source="synthetic",
        issued_at=T0, flyability=0.9, cb_flag=False,
    )
    fields.update(changes)
    return WeatherCell(**fields)


def query(**changes):
    fields = dict(route=[(26.0, 72.0)], start_time=T0, speed_kts=300, altitude_ft=8000)
    fields.update(changes)
    return GetWeatherAlongRouteInput(**fields)


def context(cells):
    snapshot = SimpleNamespace(cells=cells)
    source = SimpleNamespace(snapshot=lambda: snapshot)
    # Only the scenario source is used; scenario_time must not replace start_time.
    return ToolContext(source, None, None, None, T0 - timedelta(days=1))


def run(payload, ctx):
    return asyncio.run(get_weather_along_route(payload, ctx))


def test_schema_aliases_and_spec_remain_unchanged():
    assert GetWeatherAlongRouteInput is WeatherRouteQuery
    assert GetWeatherAlongRouteOutput is WeatherRouteResult
    assert SPEC.input_model is WeatherRouteQuery
    assert SPEC.output_model is WeatherRouteResult
    assert SPEC.name == "get_weather_along_route"
    assert SPEC.side_effects == "none"
    assert SPEC.timeout_s == 10


def test_segments_return_actual_weather_and_deduplicated_advisories():
    forecast = cell(
        cloud_low_pct=90, ceiling_ft=1800, visibility_km=1.0, wind_kts=23,
        wind_dir_deg=310, cape_jkg=1500, cb_flag=True, flyability=0.0,
    )
    result = run(query(route=[(26.1, 72.1), (26.11, 72.11)]), context([forecast]))
    assert result.segments == [
        WeatherSegment(
            time=T0, lat=lat, lon=lon, flyability=0.0, ceiling_ft=1800,
            vis_km=1.0, wind_kts=23, wind_dir_deg=310, cb_flag=True,
        )
        for lat, lon in [(26.1, 72.1), (26.11, 72.11)]
    ]
    assert result.worst_flyability == 0.0
    assert result.advisories == [
        "visibility below takeoff minimum", "ceiling below minimum",
        "cumulonimbus along route",
    ]


def test_sampling_every_25_km_keeps_route_order_and_waypoints():
    route = [(27.0, 72.0), (26.0, 72.0), (26.0, 72.1)]
    cells = [cell(lat=lat, flyability=score) for lat, score in [(26, 0.3), (26.5, 0.6), (27, 0.9)]]
    result = run(query(route=route), context(cells))
    km_per_degree = 6371.0 * math.pi / 180
    expected = [(27.0, 72.0)] + [
        (27.0 - distance / km_per_degree, 72.0) for distance in (25, 50, 75, 100)
    ] + route[1:]
    assert len(result.segments) == 7
    for segment, coordinates in zip(result.segments, expected):
        assert (segment.lat, segment.lon) == pytest.approx(coordinates)
    assert [segment.time for segment in result.segments] == [T0] * 7
    assert [segment.flyability for segment in result.segments] == [0.9, 0.9, 0.6, 0.6, 0.3, 0.3, 0.3]
    assert result.worst_flyability == 0.3
    assert result.advisories == []


@pytest.mark.parametrize("route", [[(26, 72)], [(26, 72), (26, 72), (26, 72)]])
def test_single_point_and_zero_length_legs_produce_one_sample(route):
    result = run(query(route=route), context([cell()]))
    assert len(result.segments) == 1
    assert (result.segments[0].lat, result.segments[0].lon) == (26, 72)


@pytest.mark.parametrize(
    "speed, hours",
    [
        (60, [0, 0, 0, 1, 1, 1, 1, 1, 2, 2, 2]),
        (120, [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1]),
    ],
)
def test_speed_uses_cumulative_travel_time_across_legs(speed, hours):
    cells = [
        cell(lat=lat, time=T0 + timedelta(hours=hour), flyability=score)
        for hour, score in [(0, 0.9), (1, 0.6), (2, 0.3)]
        for lat in (26, 26.5, 27)
    ]
    result = run(query(route=[(26, 72), (27, 72), (26, 72)], speed_kts=speed), context(cells))
    assert [segment.time for segment in result.segments] == [T0 + timedelta(hours=hour) for hour in hours]
    assert [segment.flyability for segment in result.segments] == [{0: 0.9, 1: 0.6, 2: 0.3}[hour] for hour in hours]
    assert result.worst_flyability == (0.3 if speed == 60 else 0.6)


@pytest.mark.parametrize(
    "start, expected",
    [
        (T0 + timedelta(minutes=29, seconds=59, microseconds=999999), T0),
        (T0 + timedelta(minutes=30), T0 + timedelta(hours=1)),
        (T0.replace(tzinfo=None), T0),
        (T0.astimezone(timezone(timedelta(hours=5, minutes=30))), T0),
    ],
)
def test_start_time_normalization_and_nearest_hour_boundary(start, expected):
    # A forecast stored in another timezone still matches its UTC hour.
    forecast_time = expected.astimezone(timezone(timedelta(hours=5, minutes=30)))
    result = run(query(start_time=start), context([cell(time=forecast_time)]))
    assert result.segments[0].time == expected
    assert result.segments[0].time.tzinfo == timezone.utc


@pytest.mark.parametrize("speed", [0, -1])
def test_nonpositive_speed_is_rejected_by_existing_schema(speed):
    with pytest.raises(ValidationError, match="speed_kts"):
        query(speed_kts=speed)


def test_empty_route_is_rejected_by_existing_schema():
    with pytest.raises(ValidationError, match="route"):
        query(route=[])


@pytest.mark.parametrize(
    "cells",
    [[], [cell(lat=72, lon=26)], [cell(time=T0 - timedelta(hours=1))], [cell(time=T0 + timedelta(hours=1))]],
)
def test_missing_cells_raise_existing_error_without_fallback(cells):
    payload = query()
    with pytest.raises(ValueError) as helper_error:
        query_weather_along_route(cells, payload)
    with pytest.raises(ValueError) as tool_error:
        run(payload, context(cells))
    assert type(tool_error.value) is type(helper_error.value)
    assert str(tool_error.value) == str(helper_error.value) == "no forecast for 26.0,72.0 at 2026-06-01T10:00:00Z"


def test_missing_interior_cell_cannot_be_covered_by_endpoint_cells():
    with pytest.raises(ValueError, match="no forecast for 26.5,72.0 at 2026-06-01T10:00:00Z"):
        run(query(route=[(26, 72), (27, 72)]), context([cell(), cell(lat=27)]))


def test_missing_later_hour_cannot_be_covered_by_departure_forecast():
    cells = [cell(lat=lat) for lat in (26, 26.5, 27)]
    with pytest.raises(ValueError, match="no forecast for 26.5,72.0 at 2026-06-01T11:00:00Z"):
        run(query(route=[(26, 72), (27, 72)], speed_kts=60), context(cells))


@pytest.mark.parametrize(
    "altitude, score, advisories",
    [
        (9999, 0.9, []),
        (10_000, 0.0, ["icing along route"]),
        (12_000, 0.0, ["icing along route"]),
        (14_000, 0.0, ["icing along route"]),
        (14_001, 0.9, []),
    ],
)
def test_altitude_icing_layer_includes_boundaries(altitude, score, advisories):
    forecast = cell(cloud_low_pct=10, cloud_mid_pct=20, cloud_high_pct=20)
    result = run(query(altitude_ft=altitude), context([forecast]))
    assert result.segments[0].flyability == score
    assert result.worst_flyability == score
    assert result.advisories == advisories


def test_altitude_inside_freezing_layer_requires_enough_cloud():
    result = run(query(altitude_ft=12_000), context([cell(cloud_high_pct=39)]))
    assert result.worst_flyability == 0.9
    assert result.advisories == []


def test_tool_reads_one_snapshot_and_does_not_mutate_query_or_cells():
    payload = query(route=[(26.1, 72.1)])
    cells = [cell()]
    before = payload.model_dump(), [item.model_dump() for item in cells]
    reads = []

    def snapshot():
        reads.append(True)
        return SimpleNamespace(cells=cells)

    ctx = ToolContext(SimpleNamespace(snapshot=snapshot), None, None, None, T0)
    result = run(payload, ctx)
    assert result == query_weather_along_route(cells, payload)
    assert reads == [True]
    assert (payload.model_dump(), [item.model_dump() for item in cells]) == before


def test_weather_graph_returns_actual_result_and_propagates_missing_coverage():
    pytest.importorskip("langgraph")
    from garuda.tools.data.graph import GRAPHS, WEATHER_GRAPH

    payload = query(route=[(26.1, 72.1)])
    ctx = context([cell(flyability=0.4, wind_kts=25)])
    assert GRAPHS["get_weather_along_route"] is WEATHER_GRAPH
    result = asyncio.run(WEATHER_GRAPH.ainvoke({"payload": payload.model_dump(mode="json"), "ctx": ctx}))
    assert result["output"] == run(payload, ctx).model_dump(mode="json")
    assert result["output"]["worst_flyability"] == 0.4
    with pytest.raises(ValueError, match="no forecast"):
        asyncio.run(WEATHER_GRAPH.ainvoke({"payload": payload.model_dump(mode="json"), "ctx": context([])}))
