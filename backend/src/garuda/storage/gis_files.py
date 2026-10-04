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


def publish_terrain(root: Path) -> int:
    endpoint = os.environ.get("MINIO_ENDPOINT")
    if not endpoint:
        return 0
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("MINIO_ROOT_USER", "garuda"),
        aws_secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "garuda-secret"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        config=Config(signature_version="s3v4"),
    )
    bucket = os.environ.get("MINIO_BUCKET", "garuda")
    keys = (DEM_NPY, DEM_KEY, MASK_NPY, PENALTY_NPY)
    for key in keys:
        client.upload_file(str(root / key), bucket, key)
    return len(keys)


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
