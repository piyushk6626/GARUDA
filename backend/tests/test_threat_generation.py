import math
from datetime import timedelta
from pathlib import Path

import pytest

from garuda.config import load_config
from garuda.generators.missions import build_scenario
from garuda.generators.threats import confidence_at, exposure_risk
from garuda.schemas.threat import Threat
from garuda.storage.kafka_publish import THREAT_TOPIC, threat_event

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"
B2_LAT = 26.89
B2_LON = 70.86
SAM_BANDS = {
    "SAM_LONG": (150.0, 250.0),
    "SAM_MED": (40.0, 80.0),
    "SAM_SHORT": (5.0, 20.0),
    "AAA": (5.0, 20.0),
    "AIRFIELD": (5.0, 20.0),
    "ENEMY_CAP": (40.0, 80.0),
}


def _scenario(seed: int = 42):
    return build_scenario(load_config(CONFIG_PATH), seed)


def _threat(**overrides) -> Threat:
    moment = load_config(CONFIG_PATH).start
    values = {
        "threat_id": "TH-TEST",
        "type": "SAM_MED",
        "lon": 70.0,
        "lat": 27.0,
        "mobile": False,
        "engagement_range_km": 50.0,
        "detection_range_km": 80.0,
        "min_alt_ft": 100,
        "max_alt_ft": 60_000,
        "pk_base": 0.5,
        "confidence": 0.8,
        "first_seen": moment,
        "last_seen": moment,
        "status": "ACTIVE",
    }
    values.update(overrides)
    return Threat.model_validate(values)


def test_fixed_seed_is_reproducible() -> None:
    first = _scenario()
    second = _scenario()
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_counts_ranges_and_western_clusters() -> None:
    config = load_config(CONFIG_PATH)
    scenario = _scenario()
    threats = scenario.threats
    popup_at = config.start + timedelta(hours=14)
    popups = [threat for threat in threats if threat.first_seen == popup_at]
    fixed = [threat for threat in threats if not threat.mobile and threat.first_seen < config.start]
    mobile_sams = [
        threat for threat in threats if threat.mobile and threat.type in {"SAM_MED", "SAM_SHORT"}
    ]
    caps = [threat for threat in threats if threat.type == "ENEMY_CAP"]
    threat_ids = {threat.threat_id for threat in threats}

    assert 12 <= len(fixed) <= 20
    assert 2 <= len(mobile_sams) <= 4
    assert 1 <= len(caps) <= 2
    assert len(popups) == 1
    assert popups[0].type == "SAM_MED"
    assert popups[0].status == "ACTIVE"
    assert abs(popups[0].lat - B2_LAT) < 0.2
    assert abs(popups[0].lon - B2_LON) < 0.2

    for threat in threats:
        assert config.bbox.lat_min <= threat.lat <= config.bbox.lat_max
        assert config.bbox.lon_min <= threat.lon < 73
        assert 0 <= threat.confidence <= 1
        assert threat.detection_range_km >= threat.engagement_range_km
        assert threat.min_alt_ft <= threat.max_alt_ft
        if threat.type in {"RADAR_EW", "RADAR_GCI"}:
            assert threat.engagement_range_km == 0
            assert threat.pk_base == 0
            assert threat.detection_range_km > 0
            continue
        low, high = SAM_BANDS[threat.type]
        assert low <= threat.engagement_range_km <= high

    for target in scenario.targets:
        assert target.defended_by
        assert set(target.defended_by) <= threat_ids


def test_confidence_decays_until_the_next_sighting() -> None:
    moment = load_config(CONFIG_PATH).start
    mobile = _threat(mobile=True, confidence=0.8, last_seen=moment)
    fixed = _threat(threat_id="TH-FIXED", mobile=False, confidence=0.8, last_seen=moment)
    later = moment + timedelta(hours=12)

    assert confidence_at(mobile, moment) == mobile.confidence
    assert confidence_at(mobile, moment - timedelta(hours=1)) == mobile.confidence
    assert confidence_at(mobile, later) == pytest.approx(mobile.confidence * math.exp(-1))
    assert confidence_at(fixed, later) == pytest.approx(fixed.confidence * math.exp(-12 / 72))
    assert confidence_at(mobile, mobile.last_seen) == mobile.confidence


def test_only_mobile_units_have_moving_tracks() -> None:
    config = load_config(CONFIG_PATH)
    scenario = _scenario()
    mobile_ids = {threat.threat_id for threat in scenario.threats if threat.mobile}
    tracked_ids = {track.threat_id for track in scenario.tracks}
    assert tracked_ids == mobile_ids
    assert mobile_ids
    assert all(
        threat.type in {"SAM_MED", "SAM_SHORT", "ENEMY_CAP"}
        for threat in scenario.threats
        if threat.mobile
    )

    for threat_id in mobile_ids:
        threat = next(item for item in scenario.threats if item.threat_id == threat_id)
        points = [track for track in scenario.tracks if track.threat_id == threat_id]
        assert points[0].lon == threat.lon
        assert points[0].lat == threat.lat
        assert points[-1].time - points[0].time == timedelta(hours=config.horizon_hours)
        assert points[1].time - points[0].time == timedelta(hours=1)
        assert len({(point.lon, point.lat) for point in points}) > 1
        for point in points:
            assert point.speed_kts > 0
            assert 0 <= point.heading < 360
            assert config.bbox.lat_min <= point.lat <= config.bbox.lat_max
            assert config.bbox.lon_min <= point.lon < 73
        if threat.type == "ENEMY_CAP":
            assert all(250 <= point.speed_kts <= 500 for point in points)
            continue
        assert all(10 <= point.speed_kts <= 40 for point in points)


def test_one_sead_site_is_suppressed() -> None:
    scenario = _scenario()
    suppressed = [threat for threat in scenario.threats if threat.status == "SUPPRESSED"]
    assert len(suppressed) == 1
    assert suppressed[0].type in {"SAM_LONG", "SAM_MED", "SAM_SHORT"}
    assert sum(threat.status == "ACTIVE" for threat in scenario.threats) == len(scenario.threats) - 1

    sead_targets = {
        mission.target_id
        for mission in scenario.missions
        if mission.type == "SEAD" and mission.target_id is not None
    }
    defenders = {
        threat_id
        for target in scenario.targets
        if target.target_id in sead_targets
        for threat_id in target.defended_by
    }
    assert suppressed[0].threat_id in defenders


def test_exposure_matches_the_product_formula() -> None:
    threat = _threat(pk_base=0.5, confidence=0.8, engagement_range_km=50, detection_range_km=80)
    assert exposure_risk(threat.lon, threat.lat, 10_000, [threat]) == pytest.approx(0.4)
    assert exposure_risk(threat.lon + 2, threat.lat, 10_000, [threat]) == 0
    assert exposure_risk(threat.lon, threat.lat, 90_000, [threat]) == 0

    other = _threat(
        threat_id="TH-OTHER",
        pk_base=0.2,
        confidence=1.0,
        engagement_range_km=50,
        detection_range_km=80,
        lon=threat.lon,
        lat=threat.lat,
    )
    expected = 1 - (1 - 0.5 * 0.8) * (1 - 0.2 * 1.0)
    assert exposure_risk(threat.lon, threat.lat, 10_000, [threat, other]) == pytest.approx(expected)


def test_threat_update_event_id_is_stable() -> None:
    threat = _scenario().threats[0]
    first = threat_event(threat)
    second = threat_event(threat)
    assert THREAT_TOPIC == "garuda.threat.update"
    assert first["event_id"] == second["event_id"]
    assert first["entity_id"] == threat.threat_id
    assert first["type"] == "threat.updated"
    assert first["version"] == 1
    assert set(first) == {"event_id", "type", "entity_id", "ts", "version", "payload"}
