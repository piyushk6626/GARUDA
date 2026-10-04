from datetime import timedelta
from pathlib import Path

from garuda.config import load_config
from garuda.generators.missions import build_scenario
from garuda.ingest.adsb import normalize_opensky_response
from garuda.queries.adsb import conflict_candidates, density_cells, five_minute_summaries, get_civil_traffic

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"


def _scenario(seed: int = 42):
    return build_scenario(load_config(CONFIG_PATH), seed)


def test_recorded_and_background_tracks() -> None:
    first = _scenario()
    second = _scenario()
    assert [track.model_dump(mode="json") for track in first.adsb_tracks] == [
        track.model_dump(mode="json") for track in second.adsb_tracks
    ]
    sources = {track.source_id for track in first.adsb_tracks}
    assert sources == {"opensky-fixture", "synthetic"}
    recorded = [track for track in first.adsb_tracks if track.source_id == "opensky-fixture"]
    assert {track.icao24 for track in recorded} == {"abc123", "def456"}
    assert any(track.alt_m is None and track.speed_ms is None for track in recorded)
    synthetic = [track for track in first.adsb_tracks if track.source_id == "synthetic"]
    assert len(synthetic) == 36
    assert sum(track.on_ground for track in synthetic) == 3
    bbox = load_config(CONFIG_PATH).bbox
    for track in synthetic:
        assert bbox.lat_min <= track.lat <= bbox.lat_max
        assert bbox.lon_min <= track.lon <= bbox.lon_max


def test_malformed_rows_do_not_drop_their_neighbors() -> None:
    document = {
        "states": [
            ["abc123", "SIM001  ", "FABRICATED", 1_700_000_000, 1_700_000_001, 73.0, 26.3, 1000.0, False, 80.0, 90.0, 0.0, None, None, None, False, 0],
            ["nope", "BAD", "FABRICATED", 1_700_000_000, 1_700_000_000, 73.0, 26.3, 1000.0, False, 80.0, 90.0, 0.0],
            ["def456", None, "FABRICATED", None, 1_700_000_000, 73.1, 26.4, None, False, None, None, None],
            ["abc124", "SIM002", "FABRICATED", 1_700_000_000, 1_700_000_000, 73.2, 26.5, None, False, None, 360.0, None],
        ]
    }
    result = normalize_opensky_response(document, source_id="unit")
    assert result.dropped == 3
    assert [track.icao24 for track in result.observations] == ["abc123"]
    assert result.observations[0].callsign == "SIM001"
    assert result.observations[0].alt_m == 1000.0
    empty = normalize_opensky_response({"states": None}, source_id="unit")
    assert empty.observations == ()
    assert empty.dropped == 0


def test_live_density_and_unknown_altitude() -> None:
    scenario = _scenario()
    start = scenario.adsb_tracks[-1].time
    bbox = load_config(CONFIG_PATH).bbox
    live = get_civil_traffic(
        scenario.adsb_tracks,
        as_of=start,
        lat_min=bbox.lat_min,
        lon_min=bbox.lon_min,
        lat_max=bbox.lat_max,
        lon_max=bbox.lon_max,
    )
    assert live["coverage"] == "synthetic_or_recorded_snapshot"
    assert live["items"]
    stale = get_civil_traffic(
        scenario.adsb_tracks,
        as_of=start + timedelta(seconds=121),
        lat_min=bbox.lat_min,
        lon_min=bbox.lon_min,
        lat_max=bbox.lat_max,
        lon_max=bbox.lon_max,
    )
    assert stale["items"] == []
    cells = density_cells(scenario.adsb_tracks)
    counted = {track.icao24 for track in scenario.adsb_tracks if not track.on_ground}
    assert sum(cell["aircraft"] for cell in cells) >= len(counted) - 5
    assert all(cell["cell_degrees"] == 0.05 for cell in cells)
    summary = five_minute_summaries(scenario.adsb_tracks)
    assert summary
    assert "heading" not in summary[0]
    missing_alt = next(track for track in scenario.adsb_tracks if track.alt_m is None)
    findings = conflict_candidates(
        scenario.adsb_tracks,
        lat=missing_alt.lat,
        lon=missing_alt.lon,
        altitude_ft=20_000,
        at=missing_alt.time,
    )
    match = next(item for item in findings if item["icao24"] == missing_alt.icao24)
    assert match["assessment"] == "unknown"
