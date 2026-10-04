import asyncio
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from pyproj import Geod
from shapely import wkt
from shapely.geometry import LineString

from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.optimization.plan_route.baseline import baseline
from garuda.optimization.plan_route.explain import cumulative_risk
from garuda.optimization.plan_route.model import Surface, assess, solve
from garuda.optimization.plan_route.problem import build_problem
from garuda.optimization.plan_route.schemas import PlanRouteInput, PlanRouteOutput, RouteWeights
from garuda.optimization.plan_route.tool import SPEC, plan_route
from garuda.schemas.aircraft import AircraftTypeSpec
from garuda.schemas.airspace import AirspaceZone
from garuda.schemas.gis import TerrainMeta
from garuda.schemas.threat import Threat, ThreatTrack
from garuda.schemas.weather import WeatherCell

T = datetime(2026, 10, 4, tzinfo=timezone.utc)
GEOD = Geod(ellps="WGS84")


def payload(**changes):
    args = dict(origin={"lat": 26, "lon": 73}, destination={"lat": 26, "lon": 74},
                aircraft_type="Tejas", depart_time=T, altitude_profile="LOW")
    args.update(changes)
    return PlanRouteInput(**args)


def threat(tid="SAM", **changes):
    args = dict(threat_id=tid, type="SAM_MED", lon=73.5, lat=26, mobile=False,
                engagement_range_km=15, detection_range_km=30, min_alt_ft=0,
                max_alt_ft=60000, pk_base=.7, confidence=1, first_seen=T,
                last_seen=T, status="ACTIVE")
    args.update(changes)
    return Threat(**args)


def zone(zid="ZONE", **changes):
    args = dict(zone_id=zid, name=zid, type="PROHIBITED",
                wkt="POLYGON((73.49 25.95,73.51 25.95,73.51 26.05,73.49 26.05,73.49 25.95))",
                floor_ft=0, ceiling_ft=60000, controlling_authority="test", source="synthetic")
    args.update(changes)
    return AirspaceZone(**args)


def weather(**changes):
    args = dict(time=T, lat=26, lon=73.5, cloud_low_pct=0, cloud_mid_pct=0,
                cloud_high_pct=0, ceiling_ft=None, visibility_km=10, wind_kts=0,
                wind_dir_deg=0, gust_kts=0, precip_mm_h=0, cape_jkg=0,
                temp_c=20, freezing_level_ft=10000, source="synthetic", issued_at=T,
                flyability=1, cb_flag=False)
    args.update(changes)
    return WeatherCell(**args)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("GARUDA_GIS_DIR", str(tmp_path))
    spec = AircraftTypeSpec(type="Tejas", fuel_capacity_kg=1000, inspection_cycle_hours=100,
                            turnaround_min=40, range_km=400, cruise_kts=400,
                            ceiling_ft=40000, capabilities=["BVR"])
    snapshot = SimpleNamespace(aircraft_types=[spec], threats=[], tracks=[], zones=[],
                               cells=[], adsb_tracks=[],
                               terrain=TerrainMeta(lat_min=24, lat_max=28, lon_min=71, lon_max=76,
                                                   cell_km=1, rows=4, cols=5,
                                                   elev_min_m=0, elev_max_m=0))
    return ToolContext(scenario=SimpleNamespace(snapshot=lambda: snapshot), plans=None,
                       models=None, providers=None, scenario_time=T, seed=42)


def run(p, ctx):
    return asyncio.run(plan_route(p, ctx))


@pytest.mark.parametrize("algorithm", ["astar", "thetastar", "rrtstar"])
def test_unobstructed_route_contract_metrics_and_no_side_effects(ctx, algorithm):
    p = payload(algorithm=algorithm)
    result = run(p, ctx)
    assert isinstance(result, PlanRouteOutput)
    assert set(result.model_dump()) == {"waypoints", "distance_km", "fuel_kg", "cumulative_risk", "airspace_crossed", "alternatives"}
    assert result.waypoints[0].lat == p.origin.lat
    assert result.waypoints[0].lon == p.origin.lon
    assert result.waypoints[-1].lat == p.destination.lat
    assert result.waypoints[-1].lon == p.destination.lon
    km = GEOD.inv(73, 26, 74, 26)[2] / 1000
    assert result.distance_km == pytest.approx(km, rel=1e-6)
    assert result.fuel_kg == pytest.approx(km / 400 * 1000, rel=1e-6)
    assert (result.waypoints[-1].eta - T).total_seconds() == pytest.approx(km / (400 * 1.852) * 3600, rel=1e-6)
    assert all(w.alt_ft == 3000 for w in result.waypoints)
    assert result.cumulative_risk == 0
    assert result.airspace_crossed == []
    assert {r.label for r in result.alternatives} == {"shortest", "safest"}
    assert SPEC.input_model is PlanRouteInput and SPEC.output_model is PlanRouteOutput
    assert SPEC.side_effects == "none" and SPEC.timeout_s == 20


@pytest.mark.parametrize("algorithm", ["astar", "thetastar", "rrtstar"])
def test_threat_detour_checks_every_segment(ctx, algorithm):
    ctx.scenario.snapshot().threats = [threat()]
    p = payload(algorithm=algorithm)
    result = run(p, ctx)
    assert result.distance_km > GEOD.inv(73, 26, 74, 26)[2] / 1000 + 1
    assert result.cumulative_risk == 0
    # Check geodesic interiors as well as returned endpoints.
    for a, b in zip(result.waypoints, result.waypoints[1:]):
        for lon, lat in [(a.lon, a.lat), *GEOD.npts(a.lon, a.lat, b.lon, b.lat, 3)]:
            assert GEOD.inv(lon, lat, 73.5, 26)[2] / 1000 >= 15 - .02


def test_narrow_prohibited_airspace_and_closed_capacity(ctx):
    s = ctx.scenario.snapshot()
    for z in [zone(), zone(type="CORRIDOR", capacity_per_hour=0)]:
        s.zones = [z]
        result = run(payload(), ctx)
        path = LineString([(w.lon, w.lat) for w in result.waypoints])
        assert not path.intersects(wkt.loads(z.wkt))
        assert z.zone_id not in result.airspace_crossed


def test_airspace_activation_at_arrival_and_altitude(ctx):
    s = ctx.scenario.snapshot()
    # Route reaches the crossing around minute four.
    s.zones = [zone(active_from=T + timedelta(minutes=3), active_to=T + timedelta(minutes=6))]
    assert baseline(build_problem(payload(), ctx)) is None
    s.zones = [zone(active_from=T + timedelta(minutes=20), active_to=T + timedelta(minutes=30))]
    assert baseline(build_problem(payload(), ctx)) is not None
    s.zones = [zone(ceiling_ft=2000)]
    assert baseline(build_problem(payload(), ctx)) is not None
    s.zones = [zone(type="CTR")]
    result = run(payload(weights=RouteWeights(threat=0, weather=0, civil=0, terrain=0)), ctx)
    assert result.airspace_crossed == ["ZONE"]


def test_auto_avoids_low_altitude_threat_and_obeys_ceiling(ctx):
    s = ctx.scenario.snapshot()
    s.threats = [threat(engagement_range_km=200, detection_range_km=200, max_alt_ft=12000)]
    result = run(payload(altitude_profile="AUTO"), ctx)
    assert all(w.alt_ft == 18000 for w in result.waypoints)
    s.aircraft_types[0].ceiling_ft = 17000
    with pytest.raises(ToolInputError, match="ceiling"):
        run(payload(altitude_profile="MEDIUM"), ctx)


def test_fallback_reports_risk_without_relaxing_airspace_or_range(ctx):
    s = ctx.scenario.snapshot()
    s.threats = [threat(engagement_range_km=300, detection_range_km=300)]
    with pytest.warns(RuntimeWarning, match="least-risk"):
        result = run(payload(), ctx)
    assert result.cumulative_risk == pytest.approx(.7)
    assert "least-risk (risk cap exceeded)" in {r.label for r in result.alternatives}
    s.zones = [zone(wkt="POLYGON((72 25,75 25,75 27,72 27,72 25))")]
    with pytest.raises(ToolInputError, match="No route"):
        run(payload(), ctx)


def test_risk_counts_distinct_threats_not_sampling_density(ctx):
    s = ctx.scenario.snapshot()
    s.threats = [threat("A", pk_base=.1), threat("B", pk_base=.2)]
    p = build_problem(payload(max_risk=1, weights=RouteWeights(threat=0, weather=0, civil=0, terrain=0)), ctx)
    route = baseline(p)
    assert cumulative_risk(p, route.mask) == pytest.approx(1 - .9 * .8)
    surface = Surface(p, 3000, p.payload.weights)
    dense = assess(surface, [(p.goal[0] * i / 100, p.goal[1] * i / 100) for i in range(101)])
    assert dense.mask == route.mask
    assert dense.cost == pytest.approx(route.cost, rel=1e-6)
    assert surface.edge((0, 0), p.goal, 0).peak_risk == pytest.approx(.28)


def test_mobile_sightings_confidence_decay_destroyed_and_future_threats(ctx):
    s = ctx.scenario.snapshot()
    s.threats = [threat(mobile=True, lon=72, first_seen=T - timedelta(hours=24), last_seen=T - timedelta(hours=12)),
                 threat("DESTROYED", status="DESTROYED"),
                 threat("FUTURE", first_seen=T + timedelta(hours=1), last_seen=T + timedelta(hours=1))]
    s.tracks = [ThreatTrack(time=T - timedelta(hours=12), threat_id="SAM", lon=73.5, lat=26,
                           speed_kts=10, heading=90),
                ThreatTrack(time=T + timedelta(hours=1), threat_id="SAM", lon=72, lat=26,
                            speed_kts=10, heading=90)]
    p = build_problem(payload(), ctx)
    assert len(p.threats) == 1
    assert p.threats[0].probability == pytest.approx(.7 / math.e)
    assert baseline(p) is not None


def test_weather_buckets_use_distance_not_weighted_cost(ctx):
    s = ctx.scenario.snapshot()
    s.cells = [weather(lat=26, lon=73, time=T, flyability=1),
               weather(lat=26, lon=73, time=T + timedelta(hours=1), flyability=.2),
               weather(lat=26, lon=73.5, time=T, flyability=1),
               weather(lat=26, lon=73.5, time=T + timedelta(hours=1), flyability=.2),
               weather(lat=26, lon=74, time=T, flyability=1),
               weather(lat=26, lon=74, time=T + timedelta(hours=1), flyability=.2)]
    p = build_problem(payload(weights=RouteWeights(threat=0, weather=100, terrain=0, civil=0)), ctx)
    surface = Surface(p, 3000, p.payload.weights)
    assert surface._weather(26, 73, T + timedelta(minutes=29)) == 0
    assert surface._weather(26, 73, T + timedelta(minutes=31)) == pytest.approx(.8)
    edge = surface.edge((0, 0), p.goal, 0)
    assert edge.cost == pytest.approx(edge.distance)


def test_terrain_clearance_and_mismatching_grid(ctx, tmp_path):
    s = ctx.scenario.snapshot()
    s.terrain.elev_max_m = 2000
    with pytest.raises(ToolInputError, match="No route"):
        run(payload(), ctx)
    result = run(payload(altitude_profile="AUTO"), ctx)
    assert all(w.alt_ft >= 18000 for w in result.waypoints)
    path = tmp_path / "processed" / "dem_1km.npy"
    path.parent.mkdir()
    np.save(path, np.zeros((1, 1)))
    with pytest.raises(ToolInputError, match="metadata"):
        run(payload(), ctx)


def test_loaded_dem_and_penalty_are_read_only(ctx, tmp_path):
    s = ctx.scenario.snapshot()
    s.terrain.elev_max_m = 2000
    directory = tmp_path / "processed"
    directory.mkdir()
    dem = np.zeros((4, 5))
    dem[0, 0] = 2000  # Away from this route.
    np.save(directory / "dem_1km.npy", dem)
    costs = directory / "cost_layers"
    costs.mkdir()
    np.save(costs / "terrain_penalty.npy", np.zeros((4, 5)))
    result = run(payload(), ctx)
    assert result.waypoints[0].alt_ft == 3000
    np.testing.assert_array_equal(np.load(directory / "dem_1km.npy"), dem)


@pytest.mark.parametrize("changes,match", [
    ({"depart_time": T.replace(tzinfo=None)}, "timezone-aware"),
    ({"aircraft_type": "unknown"}, "unknown aircraft"),
    ({"max_risk": -1}, "max_risk"),
    ({"max_risk": float("nan")}, "max_risk"),
    ({"weights": RouteWeights(weather=-1)}, "weights.weather"),
    ({"weights": RouteWeights(threat=float("inf"))}, "weights.threat"),
    ({"destination": {"lat": 26, "lon": 80}}, "range"),
])
def test_input_errors_preserve_schema(ctx, changes, match):
    with pytest.raises(ToolInputError, match=match):
        run(payload(**changes), ctx)


def test_stationary_route_and_exact_range(ctx):
    result = run(payload(destination={"lat": 26, "lon": 73}), ctx)
    assert len(result.waypoints) == 1
    assert result.distance_km == result.fuel_kg == 0
    assert result.waypoints[0].eta == T
    ctx.scenario.snapshot().aircraft_types[0].range_km = GEOD.inv(73, 26, 74, 26)[2] / 1000
    result = run(payload(), ctx)
    assert result.fuel_kg == pytest.approx(1000, rel=1e-6)


def test_solver_is_deterministic_for_seed_and_budget(ctx):
    p = build_problem(payload(algorithm="rrtstar"), ctx)
    a, b = solve(p), solve(p)
    assert a.route.points == b.route.points


def test_least_risk_fallback_actually_avoids_extra_engagement(ctx):
    s = ctx.scenario.snapshot()
    s.threats = [threat("UNAVOIDABLE", pk_base=.1, engagement_range_km=300, detection_range_km=300),
                 threat("AVOIDABLE", pk_base=.7)]
    with pytest.warns(RuntimeWarning, match="least-risk"):
        result = run(payload(max_risk=.05), ctx)
    assert result.cumulative_risk == pytest.approx(.1)
    assert next(r for r in result.alternatives if r.label == "shortest").cumulative_risk > .1


def test_weather_weight_changes_route_and_preserves_shortest_alternative(ctx):
    s = ctx.scenario.snapshot()
    s.cells = [weather(lat=lat, lon=lon, flyability=0 if (lat, lon) == (26, 73.5) else 1)
               for lat in (25.5, 26, 26.5) for lon in (73, 73.5, 74)]
    result = run(payload(weights=RouteWeights(threat=0, weather=100, civil=0, terrain=0)), ctx)
    shortest = next(r for r in result.alternatives if r.label == "shortest")
    assert result.distance_km > shortest.distance_km + 5
    assert result.waypoints[-1].eta > T


def test_civil_density_uses_recent_latest_airborne_tracks_and_altitude(ctx):
    s = ctx.scenario.snapshot()
    def track(**changes):
        values = dict(time=T, icao24="abcdef", lat=26, lon=73.5,
                      geo_alt_m=3000 * .3048, alt_m=None, on_ground=False)
        values.update(changes)
        return SimpleNamespace(**values)
    s.adsb_tracks = [track(time=T - timedelta(minutes=1), lon=72), track(),
                     track(icao24="123abc", time=T - timedelta(minutes=16)),
                     track(icao24="456abc", time=T + timedelta(minutes=1)),
                     track(icao24="789abc", on_ground=True)]
    p = build_problem(payload(weights=RouteWeights(threat=0, weather=0, civil=10, terrain=0)), ctx)
    assert len(p.traffic) == 1
    low = Surface(p, 3000, p.payload.weights).edge((0, 0), p.goal, 0)
    high = Surface(p, 18000, p.payload.weights).edge((0, 0), p.goal, 0)
    assert low.cost > low.distance
    assert high.cost == pytest.approx(high.distance)


def test_very_thin_threat_between_samples_is_detected(ctx):
    bearing, _, distance = GEOD.inv(73, 26, 74, 26)
    lon, lat, _ = GEOD.fwd(73, 26, bearing, distance * .50321)
    ctx.scenario.snapshot().threats = [threat(lon=lon, lat=lat, engagement_range_km=.005)]
    p = build_problem(payload(), ctx)
    assert baseline(p) is None  # A 10 metre ring between 500 metre samples.


def test_airspace_end_is_exclusive_and_departure_data_is_not_mutated(ctx):
    s = ctx.scenario.snapshot()
    s.zones = [zone(active_from=T - timedelta(hours=1), active_to=T)]
    before = s.zones[0].model_dump()
    result = run(payload(), ctx)
    assert result.airspace_crossed == []
    assert s.zones[0].model_dump() == before


def test_zero_cap_accepts_zero_risk_and_one_probability_is_finite(ctx):
    assert run(payload(max_risk=0), ctx).cumulative_risk == 0
    ctx.scenario.snapshot().threats = [threat(pk_base=1, engagement_range_km=300, detection_range_km=300)]
    with pytest.warns(RuntimeWarning):
        result = run(payload(max_risk=1), ctx)
    assert result.cumulative_risk == 1
    assert math.isfinite(result.distance_km)


def test_departure_without_issued_weather_is_actionable(ctx):
    ctx.scenario.snapshot().cells = [weather(issued_at=T + timedelta(minutes=1))]
    with pytest.raises(ToolInputError, match="issued"):
        run(payload(), ctx)


def test_langgraph_uses_the_real_tool_when_optional_dependency_is_present(ctx):
    pytest.importorskip("langgraph")
    from garuda.optimization.plan_route.graph import GRAPH
    output = asyncio.run(GRAPH.ainvoke({"payload": payload().model_dump(mode="json"), "ctx": ctx}))
    result = PlanRouteOutput.model_validate(output["output"])
    assert result.waypoints[-1].lon == 74
    assert result.distance_km != 420


def test_long_geographic_airspace_edge_is_not_replaced_by_projected_chord(ctx):
    # The geodesic between equal-latitude endpoints bows north into this strip.
    # Projecting only the four polygon corners used to miss the crossing.
    ctx.scenario.snapshot().zones = [zone(wkt=(
        "POLYGON((73 26.0004,74 26.0004,74 26.002,73 26.002,73 26.0004))"))]
    assert baseline(build_problem(payload(), ctx)) is None
