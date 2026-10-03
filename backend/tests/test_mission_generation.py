from datetime import timedelta
from pathlib import Path

from garuda.config import load_config
from garuda.generators.missions import PRIORITY_VALUE, build_scenario
from garuda.storage.kafka_publish import mission_event

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"
PRIORITY_SHARE = {1: 0.10, 2: 0.20, 3: 0.35, 4: 0.25, 5: 0.10}
SHARE_TOLERANCE = 0.08


def _scenario(seed: int = 42):
    return build_scenario(load_config(CONFIG_PATH), seed)


def test_fixed_seed_is_reproducible() -> None:
    first = _scenario()
    second = _scenario()
    assert [mission.mission_id for mission in first.missions] == [
        mission.mission_id for mission in second.missions
    ]
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_counts_stay_in_the_documented_ranges() -> None:
    scenario = _scenario()
    assert 8 <= len(scenario.targets) <= 15
    assert 40 <= len(scenario.missions) <= 60


def test_priority_shares_match_the_mix() -> None:
    missions = _scenario().missions
    total = len(missions)
    for priority, expected in PRIORITY_SHARE.items():
        actual = sum(mission.priority == priority for mission in missions) / total
        assert abs(actual - expected) <= SHARE_TOLERANCE
        assert all(mission.value == PRIORITY_VALUE[mission.priority] for mission in missions)


def test_cap_and_aar_have_no_target_and_cap_has_an_area() -> None:
    missions = _scenario().missions
    caps = [mission for mission in missions if mission.type == "CAP"]
    aars = [mission for mission in missions if mission.type == "AAR"]
    assert caps
    assert aars
    assert all(mission.target_id is None for mission in caps + aars)
    assert all(mission.area_wkt for mission in caps)


def test_strike_dependencies_start_thirty_minutes_after_sead() -> None:
    missions = _scenario().missions
    by_id = {mission.mission_id: mission for mission in missions}
    linked = [mission for mission in missions if mission.dependencies]
    assert linked
    for mission in linked:
        for dependency_id in mission.dependencies:
            sead = by_id[dependency_id]
            assert sead.type == "SEAD"
            assert mission.window_start - sead.window_start == timedelta(minutes=30)


def test_emergency_p1_cas_is_injected_at_t_plus_30h() -> None:
    config = load_config(CONFIG_PATH)
    missions = build_scenario(config, 42).missions
    moment = config.start + timedelta(hours=30)
    injected = [
        mission
        for mission in missions
        if mission.type == "CAS"
        and mission.priority == 1
        and mission.window_start == moment
    ]
    assert len(injected) == 1
    assert injected[0].target_id is not None


def test_targets_stay_inside_the_theatre_bbox() -> None:
    config = load_config(CONFIG_PATH)
    for target in _scenario().targets:
        assert config.bbox.lat_min <= target.lat <= config.bbox.lat_max
        assert config.bbox.lon_min <= target.lon <= config.bbox.lon_max
        assert target.defended_by


def test_mission_request_event_id_is_stable() -> None:
    mission = _scenario().missions[0]
    first = mission_event(mission)
    second = mission_event(mission)
    assert first["event_id"] == second["event_id"]
    assert first["entity_id"] == mission.mission_id
    assert first["type"] == "mission.requested"
    assert first["version"] == 1
    assert set(first) == {"event_id", "type", "entity_id", "ts", "version", "payload"}


def test_strike_family_missions_have_targets_and_isr_has_an_area() -> None:
    for mission in _scenario().missions:
        if mission.type in {"DCA", "STRIKE", "SEAD", "CAS", "AIRLIFT"}:
            assert mission.target_id
        if mission.type == "ISR":
            assert mission.area_wkt


def test_windows_are_two_to_six_hours() -> None:
    for mission in _scenario().missions:
        length = mission.window_end - mission.window_start
        assert timedelta(hours=2) <= length <= timedelta(hours=6)
