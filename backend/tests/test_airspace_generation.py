from datetime import timedelta
from pathlib import Path

from shapely import wkt as shapely_wkt

from garuda.config import load_config
from garuda.generators.missions import build_scenario
from garuda.queries.airspace import check_airspace, zones_intersecting
from garuda.schemas.airspace import is_active

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"


def _scenario(seed: int = 42):
    return build_scenario(load_config(CONFIG_PATH), seed)


def test_synthetic_zone_mix_is_stable() -> None:
    first = _scenario()
    second = _scenario()
    assert [zone.model_dump(mode="json") for zone in first.zones] == [
        zone.model_dump(mode="json") for zone in second.zones
    ]
    counts = {}
    for zone in first.zones:
        counts[zone.type] = counts.get(zone.type, 0) + 1
        assert zone.source == "synthetic"
        assert zone.controlling_authority == "IAF"
        geom = shapely_wkt.loads(zone.wkt)
        assert geom.geom_type == "Polygon"
        assert geom.is_valid and not geom.is_empty
    assert counts == {
        "CAP": 8,
        "AAR_TRACK": 3,
        "CORRIDOR": 4,
        "TRA": 2,
        "ADIZ": 1,
        "RESTRICTED": 2,
    }
    assert all(zone.width_km == 20 for zone in first.zones if zone.type in {"AAR_TRACK", "CORRIDOR"})
    assert all(zone.name.startswith("MEZ") for zone in first.zones if zone.type == "RESTRICTED")


def test_route_altitude_and_time_filters() -> None:
    scenario = _scenario()
    start = scenario.aircraft[0].updated_at
    cap = next(zone for zone in scenario.zones if zone.type == "CAP")
    point = shapely_wkt.loads(cap.wkt).representative_point()
    route = f"LINESTRING ({point.x} {point.y}, {point.x + 0.01} {point.y})"
    hits = zones_intersecting(scenario.zones, route, 20_000, start)
    assert cap.zone_id in {zone.zone_id for zone in hits}
    above = zones_intersecting(scenario.zones, route, 100_000, start)
    assert cap.zone_id not in {zone.zone_id for zone in above}
    assert zones_intersecting(scenario.zones, "LINESTRING (0 0, 0.1 0.1)", 20_000, start) == []
    tra = next(zone for zone in scenario.zones if zone.type == "TRA")
    assert tra.mission_id is not None
    assert not is_active(tra, start)
    assert is_active(tra, tra.active_from)
    assert not is_active(tra, tra.active_to)
    assert is_active(tra, tra.active_to - timedelta(seconds=1))
    findings = check_airspace(scenario.zones, route, 20_000, start)
    assert any(item["zone_id"] == cap.zone_id and item["type"] == "CAP" for item in findings)
    assert sum(zone.type == "TRA" for zone in zones_intersecting(scenario.zones, route, 20_000, start)) == 0
