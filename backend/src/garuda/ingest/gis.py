"""Load GIS fixtures into PostGIS.

Runway rows stay on the checked-in OurAirports extract. A source GeoTIFF, when
given, is resampled to the 1 km grid. Without one, the synthetic DEM is left
in place.
"""

import argparse
import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject

from garuda.bases import BASES
from garuda.config import BoundingBox, ScenarioConfig
from garuda.generators.gis import (
    TerrainGrid,
    build_bases,
    build_features,
    grid_shape,
    layers_from_elevation,
)
from garuda.storage.gis_files import gis_root, write_terrain_files
from garuda.storage.minio import publish_terrain
from garuda.storage.neo4j_sync import sync_bases
from garuda.storage.postgres import upsert_gis
from garuda.storage.redis_seed import write_base_cache
from garuda.storage.seed import neo4j_settings, postgres_dsn, redis_url


@dataclass(frozen=True)
class IngestResult:
    bases: int
    features: int
    borders: int
    resampled: bool


def resample_dem(path: Path, bbox: BoundingBox) -> TerrainGrid:
    rows, cols = grid_shape(bbox)
    dest = np.full((rows, cols), np.nan, dtype=np.float32)
    transform = from_bounds(bbox.lon_min, bbox.lat_min, bbox.lon_max, bbox.lat_max, cols, rows)
    with rasterio.open(path) as source:
        reproject(
            source=rasterio.band(source, 1),
            destination=dest,
            src_transform=source.transform,
            src_crs=source.crs,
            dst_transform=transform,
            dst_crs="EPSG:4326",
            resampling=Resampling.average,
            dst_nodata=np.nan,
        )
    elev = np.nan_to_num(np.flipud(dest), nan=0.0).astype(np.float64)
    return layers_from_elevation(bbox, elev)


async def ingest_gis(seed: int = 42, dem_path: Path | None = None) -> IngestResult:
    config = ScenarioConfig(seed=seed, bases=len(BASES))
    bases = build_bases(config, seed)
    features, borders = build_features(config.bbox)
    await upsert_gis(postgres_dsn(), bases, features, borders)
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url())
    try:
        await write_base_cache(client, bases)
    finally:
        await client.aclose()
    uri, user, password = neo4j_settings()
    await sync_bases(uri, user, password, bases)
    if dem_path is None:
        return IngestResult(bases=len(bases), features=len(features), borders=len(borders), resampled=False)
    grid = resample_dem(dem_path, config.bbox)
    root = gis_root()
    raw = root / "raw" / "srtm" / dem_path.name
    raw.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(dem_path, raw)
    write_terrain_files(grid, root)
    publish_terrain(root)
    return IngestResult(bases=len(bases), features=len(features), borders=len(borders), resampled=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Load GIS fixtures into PostGIS")
    parser.add_argument("--dem", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    result = asyncio.run(ingest_gis(seed=args.seed, dem_path=args.dem))
    mode = "resampled" if result.resampled else "synthetic-dem"
    print(
        f"gis {mode} bases={result.bases} features={result.features} borders={result.borders}"
    )


if __name__ == "__main__":
    main()
