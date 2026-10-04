import json
from datetime import datetime, timezone

import pytest

from garuda.generators.weather import metres_to_feet
from garuda.ingest.weather import SAMPLE_GRID, SAMPLE_METAR, map_metar, map_open_meteo


def test_open_meteo_fixture_maps_units_and_source() -> None:
    payload = json.loads(SAMPLE_GRID.read_text())
    issued = datetime(2026, 10, 4, tzinfo=timezone.utc)
    cells = map_open_meteo(payload, issued)
    first, second = cells

    assert len(cells) == 2
    assert first.source == "open-meteo"
    assert first.issued_at == issued
    assert first.lat == 26.5
    assert first.lon == 73.0
    assert first.visibility_km == pytest.approx(10)
    assert first.ceiling_ft is None
    assert first.cb_flag is False
    assert first.wind_kts == 8
    assert first.freezing_level_ft == pytest.approx(metres_to_feet(4500))
    assert first.flyability == 1
    assert second.visibility_km == pytest.approx(0.4)
    assert second.flyability == 0
    assert second.cb_flag is True
    assert second.ceiling_ft == pytest.approx(3320)
    assert second.cape_jkg == 1500


def test_metar_fixture_maps_station_and_crosswind() -> None:
    payload = json.loads(SAMPLE_METAR.read_text())
    observations = map_metar(payload)
    obs = observations[0]

    assert len(observations) == 1
    assert obs.source == "metar"
    assert obs.icao == "VIJO"
    assert obs.obs_time == datetime(2026, 10, 4, 6, 30, tzinfo=timezone.utc)
    assert obs.vis_km == pytest.approx(6 * 1.60934)
    assert obs.ceiling_ft is None
    assert obs.flight_category == "VFR"
    assert obs.crosswind_kts == pytest.approx(0)
    assert obs.wind_kts == 8
    assert obs.raw_text.startswith("METAR VIJO")
    assert obs.flyability == 1
