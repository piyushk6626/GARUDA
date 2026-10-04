import json
from datetime import timedelta
from pathlib import Path

import pytest

from garuda.config import load_config
from garuda.generators.missions import build_scenario
from garuda.generators.weather import flyability, metres_to_feet
from garuda.queries.weather import get_weather_along_route
from garuda.schemas.weather import WeatherCell, WeatherRouteQuery
from garuda.storage.kafka_publish import WEATHER_TOPIC, weather_events

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"


def _scenario(seed: int = 42):
    return build_scenario(load_config(CONFIG_PATH), seed)


def test_fixed_seed_keeps_mission_and_threat_ids() -> None:
    first = _scenario()
    second = _scenario()
    assert [mission.mission_id for mission in first.missions] == [
        mission.mission_id for mission in second.missions
    ]
    assert [threat.threat_id for threat in first.threats] == [
        threat.threat_id for threat in second.threats
    ]
    assert [(cell.time, cell.lat, cell.lon, cell.flyability) for cell in first.cells] == [
        (cell.time, cell.lat, cell.lon, cell.flyability) for cell in second.cells
    ]


def test_grid_covers_each_hour_and_flags_match_cape() -> None:
    config = load_config(CONFIG_PATH)
    scenario = _scenario()
    start = config.start
    lats = sorted({cell.lat for cell in scenario.cells})
    lons = sorted({cell.lon for cell in scenario.cells})
    hours = {cell.time for cell in scenario.cells}

    assert lats[0] == 22.0
    assert lats[-1] == 33.0
    assert lons[0] == 68.0
    assert lons[-1] == 78.0
    assert all(abs((right - left) - 0.5) < 1e-9 for left, right in zip(lats, lats[1:]))
    assert all(abs((right - left) - 0.5) < 1e-9 for left, right in zip(lons, lons[1:]))
    assert hours == {start + timedelta(hours=hour) for hour in range(config.horizon_hours)}
    assert len(scenario.cells) == len(lats) * len(lons) * config.horizon_hours
    assert len(scenario.observations) == 7 * config.horizon_hours
    assert all(0 <= cell.flyability <= 1 for cell in scenario.cells)
    assert all(cell.cb_flag == (cell.cape_jkg > 1000) for cell in scenario.cells)
    assert any(cell.cb_flag for cell in scenario.cells)
    assert all(cell.source == "synthetic" and cell.issued_at == start for cell in scenario.cells)


def test_low_visibility_and_b5_fog() -> None:
    assert (
        flyability(
            visibility_km=1.5,
            ceiling=5000,
            wind_kts=10,
            cape_jkg=0,
            temp_c=25,
            cloud_low_pct=10,
            cloud_mid_pct=0,
        )
        == 0
    )
    config = load_config(CONFIG_PATH)
    scenario = _scenario()
    start = config.start
    fog_times = {start + timedelta(hours=hour) for hour in range(20, 26)}
    fog_cells = [
        cell
        for cell in scenario.cells
        if cell.lat == 31.5 and cell.lon == 76.0 and cell.time in fog_times
    ]
    later = next(
        cell
        for cell in scenario.cells
        if cell.lat == 31.5 and cell.lon == 76.0 and cell.time == start + timedelta(hours=26)
    )
    viax = [obs for obs in scenario.observations if obs.icao == "VIAX"]
    at_start = next(obs for obs in viax if obs.obs_time == start)

    assert len(fog_cells) == 6
    assert all(cell.visibility_km == 0.4 and cell.flyability == 0 and cell.ceiling_ft == 200 for cell in fog_cells)
    assert later.visibility_km > 1.5
    assert at_start.flight_category == "VFR"
    assert at_start.flyability > 0
    fog_obs = [obs for obs in viax if obs.obs_time in fog_times]
    assert len(fog_obs) == 6
    assert all(obs.flight_category == "LIFR" and obs.flyability == 0 and "FG" in obs.raw_text for obs in fog_obs)


def test_route_query_sees_fog_and_clear_air() -> None:
    config = load_config(CONFIG_PATH)
    scenario = _scenario()
    fog = get_weather_along_route(
        scenario.cells,
        WeatherRouteQuery(
            route=[(31.4338, 75.7588), (31.44, 75.76)],
            start_time=config.start + timedelta(hours=20),
            speed_kts=420,
            altitude_ft=10_000,
        ),
    )
    clear = get_weather_along_route(
        scenario.cells,
        WeatherRouteQuery(
            route=[(26.0, 72.0), (26.05, 72.05)],
            start_time=config.start,
            speed_kts=420,
            altitude_ft=10_000,
        ),
    )

    assert fog.worst_flyability == 0
    assert "visibility below takeoff minimum" in fog.advisories
    assert clear.worst_flyability > 0.5
    assert "visibility below takeoff minimum" not in clear.advisories


def test_altitude_icing_reduces_flyability() -> None:
    moment = load_config(CONFIG_PATH).start
    cell = WeatherCell(
        time=moment,
        lat=26.0,
        lon=72.0,
        cloud_low_pct=60,
        cloud_mid_pct=0,
        cloud_high_pct=0,
        ceiling_ft=6440,
        visibility_km=10,
        wind_kts=10,
        wind_dir_deg=220,
        gust_kts=10,
        precip_mm_h=0,
        cape_jkg=0,
        temp_c=20,
        freezing_level_ft=10_000,
        source="synthetic",
        issued_at=moment,
        flyability=1,
        cb_flag=False,
    )
    result = get_weather_along_route(
        [cell],
        WeatherRouteQuery(
            route=[(26.0, 72.0)],
            start_time=moment,
            speed_kts=300,
            altitude_ft=12_000,
        ),
    )
    assert result.worst_flyability == 0
    assert result.advisories == ["icing along route"]


def test_weather_events_cover_the_grid_and_each_base() -> None:
    scenario = _scenario()
    first = weather_events(scenario.cells, scenario.observations)
    second = weather_events(scenario.cells, scenario.observations)
    grid = next(event for event in first if event["type"] == "weather.grid.issued")
    base = next(event for event in first if event["entity_id"] == "VIAX")

    assert WEATHER_TOPIC == "garuda.weather.update"
    assert len(first) == 8
    assert first[0]["event_id"] == second[0]["event_id"]
    assert set(grid) == {"event_id", "type", "entity_id", "ts", "version", "payload"}
    assert grid["version"] == 1
    assert grid["payload"]["source"] == "synthetic"
    assert grid["payload"]["cell_count"] == len(scenario.cells)
    assert base["type"] == "weather.base.updated"
    assert base["payload"]["icao"] == "VIAX"
    assert base["payload"]["source"] == "synthetic"


def test_metres_to_feet_matches_the_ingest_scale() -> None:
    assert metres_to_feet(4500) == pytest.approx(4500 * 3.28084)
