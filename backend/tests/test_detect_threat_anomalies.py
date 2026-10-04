import asyncio
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from garuda.contracts.geo import PolygonModel
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.ml.detect_threat_anomalies.schemas import (
    AnomalyAlert,
    DetectThreatAnomaliesInput,
    DetectThreatAnomaliesOutput,
)
from garuda.ml.detect_threat_anomalies.tool import SPEC, detect_threat_anomalies
from garuda.schemas.adsb import AdsbTrack
from garuda.schemas.threat import Threat, ThreatTrack

T = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


class ReadOnlyStore:
    def __getattr__(self, name):
        pytest.fail(f"detection must not access stores/providers: {name}")


def threat(ident="SAM", **changes):
    values = dict(
        threat_id=ident,
        type="SAM_MED",
        lat=27,
        lon=70,
        mobile=False,
        engagement_range_km=50,
        detection_range_km=80,
        min_alt_ft=0,
        max_alt_ft=60000,
        pk_base=0.5,
        confidence=0.8,
        first_seen=T - timedelta(days=3),
        last_seen=T,
        status="ACTIVE",
    )
    values.update(changes)
    return Threat(**values)


def track(ident="SAM", time=None, **changes):
    values = dict(
        threat_id=ident,
        time=time or T - timedelta(minutes=10),
        lat=27,
        lon=70,
        speed_kts=20,
        heading=90,
    )
    values.update(changes)
    return ThreatTrack(**values)


def civil(time=None, **changes):
    moment = time or T - timedelta(minutes=10)
    values = dict(
        time=moment,
        last_contact=moment,
        source_id="test",
        icao24="abc123",
        origin_country="Test",
        lat=27,
        lon=70,
        speed_ms=200,
        heading=90,
        on_ground=False,
        position_source=0,
    )
    values.update(changes)
    return AdsbTrack(**values)


def context(*, threats=(), tracks=(), adsb=(), borders=(), now=T):
    snapshot = SimpleNamespace(
        threats=list(threats),
        tracks=list(tracks),
        adsb_tracks=list(adsb),
        borders=list(borders),
    )
    store = ReadOnlyStore()
    return ToolContext(
        scenario=SimpleNamespace(snapshot=lambda: snapshot),
        plans=store,
        models=store,
        providers=store,
        scenario_time=now,
        seed=42,
    )


def run(ctx, **values):
    return asyncio.run(
        detect_threat_anomalies(DetectThreatAnomaliesInput(**values), ctx)
    )


def density_context(count=10):
    cutoff = T - timedelta(hours=1)
    sites = [threat(f"CAP-{i}", type="ENEMY_CAP", mobile=True) for i in range(count)]
    history = [
        track(
            "CAP-0",
            cutoff - timedelta(hours=24) + timedelta(minutes=30 * i + 5),
            speed_kts=400,
        )
        for i in range(48)
    ]
    recent = [track(f"CAP-{i}", speed_kts=400) for i in range(count)]
    return context(threats=sites, tracks=history + recent)


def test_contract_and_spec_unchanged():
    assert list(DetectThreatAnomaliesInput.model_fields) == [
        "area",
        "lookback_hours",
        "sensitivity",
    ]
    assert DetectThreatAnomaliesInput().model_dump() == dict(
        area=None, lookback_hours=6, sensitivity="medium"
    )
    assert list(DetectThreatAnomaliesOutput.model_fields) == [
        "alerts",
        "baseline_period",
    ]
    assert list(AnomalyAlert.model_fields) == [
        "id",
        "type",
        "location",
        "score",
        "evidence",
        "suggested_threat_update",
    ]
    assert (
        SPEC.name == "detect_threat_anomalies"
        and SPEC.timeout_s == 20
        and SPEC.side_effects == "none"
    )
    assert (
        SPEC.input_model is DetectThreatAnomaliesInput
        and SPEC.output_model is DetectThreatAnomaliesOutput
    )


def test_empty_and_sparse_history_do_not_fabricate_alerts():
    assert run(context()).alerts == []
    output = run(context(threats=[threat()], tracks=[track()]))
    assert output.alerts == []
    assert output.baseline_period.startswith("unavailable")


def test_sam_jump_uses_prior_track_and_does_not_mutate_inputs():
    rows = [
        track(time=T - timedelta(hours=7)),
        track(time=T - timedelta(minutes=11)),
        track(lon=73),
    ]
    ctx = context(threats=[threat()], tracks=rows)
    before = [row.model_dump_json() for row in rows]
    request = DetectThreatAnomaliesInput()
    result = asyncio.run(detect_threat_anomalies(request, ctx))
    assert len(result.alerts) == 1
    alert = result.alerts[0]
    assert (
        alert.type == "sam_relocation"
        and alert.suggested_threat_update == "position_update"
    )
    assert "displacement_speed" in alert.evidence
    assert alert.location.lon == 73 and 0 <= alert.score <= 1
    assert [row.model_dump_json() for row in rows] == before
    assert request == DetectThreatAnomaliesInput()
    assert run(ctx).model_dump() == result.model_dump()


def test_constant_velocity_civil_track_is_quiet_and_civil_jump_is_review_only():
    rows = []
    for i in range(20):
        moment = T - timedelta(minutes=100 - i * 5)
        lon = 70 + (200 * 3.6 * i / 12) / (111.195 * math.cos(math.radians(27)))
        rows.append(civil(time=moment, lon=lon))
    assert run(context(adsb=rows), lookback_hours=1).alerts == []
    rows.append(civil(lon=90, time=T - timedelta(minutes=1)))
    result = run(context(adsb=rows), lookback_hours=1)
    assert result.alerts[0].type == "civil_track_deviation"
    assert result.alerts[0].suggested_threat_update == "review_civil_track"
    assert "kalman_innovation" in result.alerts[0].evidence


def test_density_ensemble_and_historical_baseline():
    ctx = density_context()
    result = run(ctx, lookback_hours=1)
    alerts = [a for a in result.alerts if a.type == "cap_density"]
    assert len(alerts) == 1
    assert {"z_score", "cusum"} <= set(alerts[0].evidence)
    assert result.baseline_period.endswith((T - timedelta(hours=1)).isoformat())
    assert "baseline_mean=1.000" in " ".join(alerts[0].evidence)
    # The historical regime produces no count anomaly when it continues.
    assert run(density_context(1), lookback_hours=1).alerts == []


def test_new_ground_cluster_with_persistence_and_known_site_exclusion():
    rows = [
        track(f"UNKNOWN-{i}", T - timedelta(minutes=25 - i), lon=73 + i * 0.0001)
        for i in range(12)
    ]
    ctx = context(tracks=rows)
    result = run(ctx)
    clusters = [a for a in result.alerts if a.type == "new_cluster"]
    assert len(clusters) == 1 and clusters[0].suggested_threat_update == "new_sam"
    assert "new_cluster" in clusters[0].evidence
    assert run(context(threats=[threat(lon=73)], tracks=rows)).alerts == []
    assert run(context(tracks=rows[:2])).alerts == []
    # Retransmissions cannot supply the minimum number of distinct detections.
    assert run(context(tracks=[rows[0]] * 20)).alerts == []


def test_cluster_correlated_with_count_detectors():
    cutoff = T - timedelta(hours=1)
    baseline = [
        track("OLD", cutoff - timedelta(hours=24) + timedelta(minutes=30 * i + 5))
        for i in range(48)
    ]
    new = [track(f"NEW-{i}", lon=73) for i in range(6)]
    result = run(context(tracks=baseline + new), lookback_hours=1)
    clusters = [a for a in result.alerts if a.type == "new_cluster"]
    assert len(clusters) == 1
    assert "new_cluster" in clusters[0].evidence and "z_score" in clusters[0].evidence


def test_lookback_future_destroyed_and_ground_civil_are_filtered():
    future_site = threat(
        "FUTURE", first_seen=T + timedelta(hours=1), last_seen=T + timedelta(hours=1)
    )
    ctx = context(
        threats=[threat(), threat("DEAD", status="DESTROYED"), future_site],
        tracks=[
            track(time=T - timedelta(hours=8), speed_kts=10000),
            track(time=T + timedelta(seconds=1), speed_kts=10000),
            track("DEAD", speed_kts=10000),
            track("FUTURE", speed_kts=10000),
        ],
        adsb=[civil(on_ground=True, speed_ms=10000)],
    )
    assert run(ctx).alerts == []
    assert run(context(tracks=[track(speed_kts=10000)]), lookback_hours=1).alerts


def test_polygon_lat_lon_order_boundary_and_previous_outside_area():
    polygon = PolygonModel(ring=[(26, 72), (26, 74), (28, 74), (28, 72)])
    ctx = context(
        threats=[threat()],
        tracks=[track(time=T - timedelta(minutes=11)), track(lon=72)],
    )
    assert run(ctx, area=polygon).alerts[0].location.lon == 72
    outside = PolygonModel(ring=[(0, 0), (0, 1), (1, 1), (1, 0)])
    assert run(ctx, area=outside).alerts == []


def test_antimeridian_polygon():
    polygon = PolygonModel(ring=[(26, 179), (26, -179), (28, -179), (28, 179)])
    ctx = context(tracks=[track(lon=-179.5, speed_kts=3000)])
    assert run(ctx, area=polygon).alerts
    assert (
        run(context(tracks=[track(lon=0, speed_kts=3000)]), area=polygon).alerts == []
    )


@pytest.mark.parametrize("hours", [0, -1, 10**10])
def test_invalid_lookback_is_actionable(hours):
    with pytest.raises(ToolInputError, match="lookback_hours"):
        run(context(), lookback_hours=hours)


@pytest.mark.parametrize(
    "ring",
    [
        [(0, 0), (0, 1), (0, 2)],
        [(0, 0), (1, 1), (0, 1), (1, 0)],
        [(100, 0), (0, 1), (1, 0)],
        [(0, float("nan")), (0, 1), (1, 0)],
    ],
)
def test_invalid_polygon_is_actionable(ring):
    with pytest.raises(ToolInputError, match="area"):
        run(context(), area=PolygonModel(ring=ring))


def test_sensitivity_changes_threshold_without_changing_schema():
    ctx = context(threats=[threat()], tracks=[track(speed_kts=120)])
    assert run(ctx, sensitivity="low").alerts == []
    assert run(ctx, sensitivity="medium").alerts == []
    assert len(run(ctx, sensitivity="high").alerts) == 1


def test_dedupe_10km_one_hour_and_separate_anomaly_types():
    rows = [
        track("SAM", T - timedelta(minutes=55 - i * 5), speed_kts=1000)
        for i in range(10)
    ]
    rows += [
        track("FAR", T - timedelta(minutes=5), lon=73, speed_kts=1000),
        track("SAM", T - timedelta(hours=2), speed_kts=1000),
    ]
    result = run(
        context(
            threats=[threat(), threat("FAR", lon=73)],
            tracks=rows,
            adsb=[civil(speed_ms=1000)],
        )
    )
    assert len([a for a in result.alerts if a.type == "sam_relocation"]) == 3
    assert len([a for a in result.alerts if a.type == "civil_track_deviation"]) == 1
    assert len({a.id for a in result.alerts}) == len(result.alerts)


def test_input_order_naive_time_and_duplicates_do_not_change_results():
    ctx = density_context()
    original = run(ctx, lookback_hours=1)
    snapshot = ctx.scenario.snapshot()
    snapshot.tracks = list(reversed(snapshot.tracks)) + snapshot.tracks[:10]
    snapshot.threats.reverse()
    assert run(ctx, lookback_hours=1).model_dump() == original.model_dump()
    ctx_naive = context(
        threats=snapshot.threats, tracks=snapshot.tracks, now=T.replace(tzinfo=None)
    )
    assert run(ctx_naive, lookback_hours=1).model_dump() == original.model_dump()


def test_future_history_cannot_train_or_change_alerts():
    ctx = density_context()
    before = run(ctx, lookback_hours=1)
    snapshot = ctx.scenario.snapshot()
    snapshot.tracks.extend(
        [track("CAP-0", T + timedelta(hours=i), speed_kts=10000) for i in range(1, 25)]
    )
    assert run(ctx, lookback_hours=1).model_dump() == before.model_dump()


def test_old_unknown_ground_location_is_not_a_new_cluster():
    rows = [track("OLD", T - timedelta(hours=8))]
    rows.extend(track(f"OTHER-{i}", T - timedelta(minutes=25 - i)) for i in range(12))
    result = run(context(tracks=rows))
    assert not [a for a in result.alerts if a.type == "new_cluster"]


def test_short_partial_window_does_not_extrapolate_single_track_spike():
    ctx = density_context(1)
    snapshot = ctx.scenario.snapshot()
    snapshot.tracks[-1] = track("CAP-0", T + timedelta(seconds=1), speed_kts=400)
    partial = context(
        threats=snapshot.threats, tracks=snapshot.tracks, now=T + timedelta(seconds=2)
    )
    assert run(partial, lookback_hours=1).alerts == []


def test_forest_fits_only_historical_features_and_uses_seed(monkeypatch):
    from sklearn.ensemble import IsolationForest

    matrices = []
    original = IsolationForest.fit

    def capture(self, matrix, *args, **kwargs):
        assert self.random_state == 42 and self.contamination == 0.02
        matrices.append(matrix)
        return original(self, matrix, *args, **kwargs)

    monkeypatch.setattr(IsolationForest, "fit", capture)
    ctx = density_context()
    run(ctx, lookback_hours=1)
    assert len(matrices) == 1 and len(matrices[0]) >= 16
    assert all(
        row[0] == 1 for row in matrices[0]
    )  # Recent ten-track spike is held out.


def test_optional_ml_dependency_errors_are_actionable(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "sklearn.cluster", None)
    rows = [track(f"UNKNOWN-{i}", lon=73) for i in range(12)]
    with pytest.raises(RuntimeError, match=r"garuda\[ml\]"):
        run(context(tracks=rows))
    # Kinematic detection remains usable without fitting a forest or clustering.
    assert run(context(tracks=[track(speed_kts=3000)])).alerts


def test_generated_scenario_with_borders_runs_deterministically():
    from pathlib import Path
    from garuda.config import load_config
    from garuda.generators.missions import build_scenario

    config = load_config(
        Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"
    )
    scenario = build_scenario(config, 42)
    before = scenario.model_dump_json()
    ctx = ToolContext(
        scenario=SimpleNamespace(snapshot=lambda: scenario),
        plans=ReadOnlyStore(),
        models=ReadOnlyStore(),
        providers=ReadOnlyStore(),
        scenario_time=config.start + timedelta(hours=24),
        seed=42,
    )
    first = run(ctx)
    assert run(ctx) == first
    assert (
        DetectThreatAnomaliesOutput.model_validate_json(first.model_dump_json())
        == first
    )
    assert all(0 <= alert.score <= 1 for alert in first.alerts)
    assert scenario.model_dump_json() == before
