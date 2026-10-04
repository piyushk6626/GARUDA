"""Write the elevation grid and route cost layers as memmaps and a GeoTIFF."""

import os
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from garuda.generators.gis import TerrainGrid

DEM_KEY = "processed/dem_1km.tif"
DEM_NPY = "processed/dem_1km.npy"
MASK_NPY = "processed/cost_layers/terrain_mask.npy"
PENALTY_NPY = "processed/cost_layers/terrain_penalty.npy"


def gis_root() -> Path:
    override = os.environ.get("GARUDA_GIS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[4] / "data" / "gis"


def write_terrain_files(grid: TerrainGrid, root: Path) -> None:
    (root / "raw" / "srtm").mkdir(parents=True, exist_ok=True)
    _memmap(root / DEM_NPY, grid.elev_m)
    _memmap(root / MASK_NPY, grid.terrain_mask)
    _memmap(root / PENALTY_NPY, grid.terrain_penalty)
    _geotiff(root / DEM_KEY, grid)


def _memmap(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    stored = np.lib.format.open_memmap(path, mode="w+", dtype=np.float32, shape=values.shape)
    stored[:] = values.astype(np.float32)
    stored.flush()
    del stored


def _geotiff(path: Path, grid: TerrainGrid) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dlat = (grid.lat_max - grid.lat_min) / grid.rows
    dlon = (grid.lon_max - grid.lon_min) / grid.cols
    transform = from_origin(grid.lon_min, grid.lat_max, dlon, dlat)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=grid.rows,
        width=grid.cols,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
    ) as dataset:
        dataset.write(np.flipud(grid.elev_m).astype(np.float32), 1)
