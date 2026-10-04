import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from shapely import wkt as shapely_wkt
from shapely.geometry import Point, shape

from garuda.config import BoundingBox, load_config
from garuda.generators.gis import (
    AIRPORTS_CSV,
    FIXTURE_DIR,
    RUNWAYS_CSV,
    build_bases,
    build_features,
    build_terrain,
    feet_to_metres,
    great_circle_km,
    grid_shape,
)
from garuda.generators.missions import build_scenario
from garuda.queries.gis import distance_matrix, spatial_query
from garuda.schemas.gis import DistanceMatrixQuery, GeoRef, SpatialQuery
from garuda.storage.gis_files import write_terrain_files

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"


def _scenario(seed: int = 42):
    return build_scenario(load_config(CONFIG_PATH), seed)


def test_fixed_seed_capacities_and_distances() -> None:
    first = _scenario()
    second = _scenario()
    other = _scenario(7)

    assert [(base.base_id, base.shelter_count, base.fuel_capacity_l) for base in first.bases] == [
        (base.base_id, base.shelter_count, base.fuel_capacity_l) for base in second.bases
    ]
    assert [row.distance_km for row in first.distances] == [row.distance_km for row in second.distances]
    assert any(
        left.shelter_count != right.shelter_count or left.fuel_capacity_l != right.fuel_capacity_l
        for left, right in zip(first.bases, other.bases)
    )
    assert [base.runway_length_m for base in first.bases] == [base.runway_length_m for base in other.bases]


def test_runway_lengths_come_from_the_open_ourairports_rows() -> None:
    config = load_config(CONFIG_PATH)
    bases = {base.icao: base for base in build_bases(config, 42)}

    assert feet_to_metres(9005) == 2745
    assert feet_to_metres(9224) == 2811
    assert feet_to_metres(9000) == 2743
    assert bases["VIJO"].runway_count == 1
    assert bases["VIJO"].runway_length_m == 2745
    assert bases["VIAM"].runway_count == 1
    assert bases["VIAM"].runway_length_m == 2811
    assert bases["VIAG"].runway_count == 2
    assert bases["VIAG"].runway_length_m == 2743
    assert bases["VIJO"].elevation_ft == 717
    assert bases["VIJO"].lat == 26.2511
    assert all(base.shelter_count >= 8 for base in bases.values())
    assert all(base.max_sorties_per_hour > 0 for base in bases.values())
    assert AIRPORTS_CSV.is_file() and RUNWAYS_CSV.is_file()


def test_features_include_cities_roads_river_and_border() -> None:
    scenario = _scenario()
    kinds = {feature.kind for feature in scenario.features}
    ids = [feature.feature_id for feature in scenario.features]

    assert kinds == {"city", "road", "river"}
    assert len(ids) == len(set(ids))
    assert any(feature.feature_id == "city:jodhpur" for feature in scenario.features)
    assert any(feature.feature_id == "river:luni" for feature in scenario.features)
    assert len(scenario.borders) == 1
    assert scenario.borders[0].border_id == "IN-PK"
    assert scenario.borders[0].kind == "international"
    assert scenario.borders[0].wkt.startswith("MULTILINESTRING")
    assert len(scenario.distances) == len(scenario.bases) * len(scenario.targets)


def test_india_outline_includes_kashmir_and_ladakh() -> None:
    payload = json.loads((FIXTURE_DIR / "india_outline.geojson").read_text())
    outline = shape(payload["features"][0]["geometry"])
    assert outline.contains(Point(74.80, 34.08))  # Srinagar
    assert outline.contains(Point(77.58, 34.16))  # Leh
    assert outline.contains(Point(74.31, 35.92))  # Gilgit, inside the Indian claim
    assert outline.bounds[3] > 36.5


def test_terrain_grid_is_one_kilometre_and_seed_stable() -> None:
    config = load_config(CONFIG_PATH)
    bbox = config.bbox
    first = build_terrain(bbox, 42)
    second = build_terrain(bbox, 42)
    other = build_terrain(bbox, 43)
    scenario = _scenario()

    assert grid_shape(bbox) == (first.rows, first.cols)
    assert first.rows > 1100
    assert first.cols > 900
    assert scenario.terrain.rows == first.rows
    assert scenario.terrain.cols == first.cols
    assert scenario.terrain.cell_km == 1
    assert np.array_equal(first.elev_m, second.elev_m)
    assert not np.array_equal(first.elev_m, other.elev_m)
    assert np.all(first.slope_deg >= 0)
    assert float(first.terrain_mask.min()) == pytest.approx(0)
    assert float(first.terrain_mask.max()) == pytest.approx(1)
    assert float(first.terrain_penalty.min()) >= 0
    assert float(first.terrain_penalty.max()) <= 1
    assert first.elev_m[first.rows // 2 :, :].mean() > first.elev_m[: first.rows // 2, :].mean()


def test_terrain_files_roundtrip_a_small_bbox(tmp_path: Path) -> None:
    bbox = BoundingBox(lat_min=26.0, lat_max=26.2, lon_min=73.0, lon_max=73.2)
    grid = build_terrain(bbox, 42)
    write_terrain_files(grid, tmp_path)

    loaded = np.load(tmp_path / "processed" / "dem_1km.npy", mmap_mode="r")
    mask = np.load(tmp_path / "processed" / "cost_layers" / "terrain_mask.npy", mmap_mode="r")
    penalty = np.load(tmp_path / "processed" / "cost_layers" / "terrain_penalty.npy", mmap_mode="r")
    assert loaded.shape == grid.elev_m.shape == mask.shape == penalty.shape
    assert loaded[0, 0] == pytest.approx(float(grid.elev_m[0, 0]), abs=1e-3)
    assert (tmp_path / "raw" / "srtm").is_dir()

    with rasterio.open(tmp_path / "processed" / "dem_1km.tif") as dataset:
        band = dataset.read(1)
    assert band.shape == grid.elev_m.shape
    assert band[0, 0] == pytest.approx(float(grid.elev_m[-1, 0]), abs=1e-3)
    assert band[-1, 0] == pytest.approx(float(grid.elev_m[0, 0]), abs=1e-3)


def test_spatial_query_and_distance_matrix() -> None:
    scenario = _scenario()
    jodhpur = next(base for base in scenario.bases if base.base_id == "B1")
    jaisalmer = next(base for base in scenario.bases if base.base_id == "B2")
    target = scenario.targets[0]
    expected = round(great_circle_km(jodhpur.lat, jodhpur.lon, jaisalmer.lat, jaisalmer.lon), 3)

    distance = spatial_query(
        SpatialQuery(
            op="distance_km",
            a=GeoRef(entity_id="B1"),
            b=GeoRef(entity_id="B2"),
        ),
        scenario.bases,
        scenario.features,
        scenario.borders,
        scenario.targets,
    )
    nearby = spatial_query(
        SpatialQuery(op="within_radius", a=GeoRef(entity_id="B1"), radius_km=20),
        scenario.bases,
        scenario.features,
        scenario.borders,
        scenario.targets,
    )
    nearest = spatial_query(
        SpatialQuery(op="nearest", a=GeoRef(entity_id="B1")),
        scenario.bases,
        scenario.features,
        scenario.borders,
        scenario.targets,
    )
    crossing = spatial_query(
        SpatialQuery(
            op="intersects",
            a=GeoRef(lat=26.45, lon=74.64),
            b=GeoRef(entity_id="river:luni"),
        ),
        scenario.bases,
        scenario.features,
        scenario.borders,
        scenario.targets,
    )
    buffered = spatial_query(
        SpatialQuery(op="buffer", a=GeoRef(entity_id="B1"), radius_km=15),
        scenario.bases,
        scenario.features,
        scenario.borders,
        scenario.targets,
    )
    matrix = distance_matrix(
        scenario.distances,
        DistanceMatrixQuery(base_ids=["B1"], target_ids=[target.target_id]),
    )

    assert distance.distance_km == expected
    assert {hit.entity_id for hit in nearby.hits} >= {"B1", "city:jodhpur"}
    assert nearest.hits[0].distance_km is not None
    assert nearest.hits[0].distance_km < 10
    assert "jodhpur" in nearest.hits[0].entity_id
    assert crossing.hits[0].entity_id == "river:luni"
    assert buffered.wkt is not None
    assert shapely_wkt.loads(buffered.wkt).intersects(shapely_wkt.loads(
        next(feature.wkt for feature in scenario.features if feature.feature_id == "city:jodhpur")
    ))
    assert matrix.kilometres == [
        [round(great_circle_km(jodhpur.lat, jodhpur.lon, target.lat, target.lon), 3)]
    ]
    with pytest.raises(ValueError, match="unknown base_id B9"):
        distance_matrix(
            scenario.distances,
            DistanceMatrixQuery(base_ids=["B9"], target_ids=[target.target_id]),
        )
    with pytest.raises(ValueError, match="unknown base_id B9"):
        spatial_query(
            SpatialQuery(op="nearest", a=GeoRef(entity_id="B9")),
            scenario.bases,
            scenario.features,
            scenario.borders,
            scenario.targets,
        )


def test_geography_helpers_cover_the_fixture_bbox() -> None:
    features, borders = build_features(BoundingBox())
    assert borders
    assert any(feature.kind == "road" for feature in features)
