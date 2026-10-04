from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from garuda.config import BoundingBox
from garuda.generators.gis import AIRPORTS_CSV, RUNWAYS_CSV, feet_to_metres, grid_shape, map_runways
from garuda.ingest.gis import resample_dem


def test_runway_fixture_skips_closed_and_converts_feet() -> None:
    stats = map_runways(AIRPORTS_CSV, RUNWAYS_CSV)

    assert set(stats) == {"VABJ", "VIAG", "VIAM", "VIAX", "VIBK", "VIJO", "VIJR"}
    assert stats["VIJO"] == (1, feet_to_metres(9005))
    assert stats["VIAM"] == (1, feet_to_metres(9224))
    assert stats["VIAG"] == (2, feet_to_metres(9000))
    assert stats["VABJ"] == (1, feet_to_metres(8205))
    assert feet_to_metres(9005) == 2745


def test_resample_dem_averages_a_source_geotiff(tmp_path: Path) -> None:
    bbox = BoundingBox(lat_min=26.0, lat_max=27.0, lon_min=72.0, lon_max=73.0)
    path = tmp_path / "source.tif"
    transform = from_bounds(72.0, 26.0, 73.0, 27.0, 4, 4)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
    ) as dataset:
        dataset.write(np.full((1, 4, 4), 250, dtype=np.float32))

    grid = resample_dem(path, bbox)

    assert grid.elev_m.shape == grid_shape(bbox)
    assert grid.rows > 100
    assert float(np.median(grid.elev_m)) == pytest.approx(250, abs=1)
    assert np.all(grid.slope_deg >= 0)
    assert float(grid.terrain_mask.min()) >= 0
    assert float(grid.terrain_mask.max()) <= 1
