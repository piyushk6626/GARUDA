"""Historical ADS-B tool filtering, latest positions, and output fidelity."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.queries.adsb import get_civil_traffic as live_traffic
from garuda.schemas.adsb import AdsbTrack
from garuda.tools.data.get_civil_traffic import GetCivilTrafficInput, get_civil_traffic

WHEN = datetime(2026, 6, 1, 6, tzinfo=timezone.utc)
POLYGON = [(28.0, 77.0), (28.0, 79.0), (30.0, 79.0), (30.0, 77.0)]


def _track(icao24="abc123", *, age_s=30, **overrides):
    values = dict(
        time=WHEN - timedelta(seconds=age_s),
        source_id="recorded",
        icao24=icao24,
        callsign="AIC101",
        origin_country="India",
        last_contact=WHEN,
        lat=28.5,
        lon=77.5,
        alt_m=10000.0,
        on_ground=False,
        position_source=0,
    )
    values.update(overrides)
    return AdsbTrack(**values)


def _ctx(tracks, *, at=WHEN):
    snapshot = SimpleNamespace(adsb_tracks=tracks)
    blank = SimpleNamespace()
    return ToolContext(
        scenario=SimpleNamespace(snapshot=lambda: snapshot),
        plans=blank,
        models=blank,
        providers=blank,
        scenario_time=at,
    )


def _query(tracks, *, since=None, polygon=POLYGON, at=WHEN):
    payload = GetCivilTrafficInput(
        polygon=polygon,
        since=since if since is not None else WHEN - timedelta(hours=1),
    )
    return asyncio.run(get_civil_traffic(payload, _ctx(tracks, at=at)))


def test_duplicate_observations_use_latest_position_independent_of_order():
    old = _track(age_s=60, callsign="OLD", alt_m=9000.0)
    latest = _track(age_s=10, callsign="NEW", lat=29.0, lon=78.0, alt_m=11000.0)
    other = _track("000001")

    for tracks in ([latest, old, other], [other, old, latest]):
        result = _query(tracks)
        assert result.total == 2
        assert [item.icao24 for item in result.items] == ["000001", "abc123"]
        assert result.items[1].model_dump() == dict(
            icao24="abc123", callsign="NEW", lat=29.0, lon=78.0, alt_m=11000.0
        )


def test_latest_position_outside_polygon_does_not_resurrect_an_old_position():
    old_inside = _track(age_s=60)
    latest_outside = _track(age_s=10, lon=80.0)

    result = _query([old_inside, latest_outside])

    assert result.total == 0
    assert result.items == []


def test_latest_track_preserves_source_identity_and_last_row_on_ties():
    first = _track(callsign="FIRST")
    last = _track(callsign="LAST")
    other_source = _track(source_id="synthetic", callsign="OTHER")

    result = _query([other_source, first, last])

    assert result.total == 2
    assert [item.callsign for item in result.items] == ["LAST", "OTHER"]


def test_since_is_inclusive_and_stale_history_is_not_filtered_by_live_freshness():
    since = WHEN - timedelta(minutes=10)
    at_since = _track("000001", time=since)
    stale = _track("000002", age_s=121)
    too_old = _track("000003", time=since - timedelta(microseconds=1))
    at_clock = _track("000004", time=WHEN)
    future = _track("000005", time=WHEN + timedelta(microseconds=1))

    result = _query([future, too_old, stale, at_clock, at_since], since=since)

    assert result.total == 3
    assert [item.icao24 for item in result.items] == ["000001", "000002", "000004"]
    live = live_traffic(
        [stale], as_of=WHEN, lat_min=28, lon_min=77, lat_max=30, lon_max=79
    )
    assert live["items"] == []
    # Historical observations remain evidence even when no live position is fresh.
    assert _query([stale], since=since).total == 1


def test_future_observation_does_not_hide_latest_position_at_scenario_time():
    current = _track(callsign="CURRENT")
    future = _track(age_s=-30, callsign="FUTURE", lon=80.0)

    result = _query([current, future])

    assert result.total == 1
    assert result.items[0].callsign == "CURRENT"


def test_since_accepts_equivalent_timezone_offsets():
    since = (WHEN - timedelta(seconds=30)).astimezone(timezone(timedelta(hours=5, minutes=30)))

    assert _query([_track()], since=since).total == 1


def test_exact_concave_polygon_excludes_a_point_inside_its_bounding_box():
    polygon = [(28, 77), (28, 79), (29, 79), (29, 78), (30, 78), (30, 77)]
    inside = _track("000001", lat=28.5, lon=78.5)
    notch = _track("000002", lat=29.5, lon=78.5)
    edge = _track("000003", lat=29.5, lon=78.0)
    vertex = _track("000004", lat=29.0, lon=78.0)

    for ring in (polygon, polygon + [polygon[0]], list(reversed(polygon))):
        result = _query([notch, vertex, inside, edge], polygon=ring)
        assert result.total == 3
        assert [item.icao24 for item in result.items] == ["000001", "000003", "000004"]


def test_nullable_callsign_and_altitude_stay_null_and_altitude_stays_in_metres():
    unknown = _track("000001", callsign=None, alt_m=None, geo_alt_m=12000.0)
    known = _track("000002", alt_m=3048.0)
    ground = _track("000003", on_ground=True, alt_m=0.0)

    result = _query([unknown, known, ground])

    assert result.total == 3
    assert result.items[0].model_dump(mode="json") == dict(
        icao24="000001", callsign=None, lat=28.5, lon=77.5, alt_m=None
    )
    assert [item.alt_m for item in result.items] == [None, 3048.0, 0.0]


def test_empty_snapshot_and_no_matching_history_return_no_fabricated_tracks():
    assert _query([]).model_dump() == {"total": 0, "items": []}
    assert _query([_track(age_s=3601)]).model_dump() == {"total": 0, "items": []}


def test_results_are_not_silently_capped_at_live_helpers_200_track_limit():
    result = _query([_track(f"{index:06x}") for index in range(205)])

    assert result.total == len(result.items) == 205


@pytest.mark.parametrize(
    "polygon",
    [
        [],
        [(28, 77), (29, 78)],
        [(28, 77)] * 3,
        [(28, 77), (29, 78), (30, 79)],
        [(28, 77), (30, 79), (28, 79), (30, 77)],
        [(91, 77), (29, 78), (30, 77)],
        [(28, 181), (29, 78), (30, 77)],
        [(28, float("nan")), (29, 78), (30, 77)],
        [(float("inf"), 77), (29, 78), (30, 77)],
    ],
)
def test_invalid_polygons_raise_actionable_errors(polygon):
    with pytest.raises(ToolInputError, match="polygon"):
        _query([], polygon=polygon)


def test_naive_since_and_scenario_time_raise_actionable_errors():
    with pytest.raises(ToolInputError, match="since must be timezone-aware"):
        _query([], since=WHEN.replace(tzinfo=None))
    with pytest.raises(ToolInputError, match="scenario_time must be timezone-aware"):
        _query([], at=WHEN.replace(tzinfo=None))


def test_since_after_scenario_time_raises_actionable_error():
    with pytest.raises(ToolInputError, match="since must not be after scenario_time"):
        _query([], since=WHEN + timedelta(seconds=1))
