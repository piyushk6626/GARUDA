"""MinIO / S3 uploads: terrain layers, raw source files, and the threat risk raster."""

import io
import json
import os
from datetime import timezone
from pathlib import Path

import numpy as np
from shapely import wkt
from shapely.geometry import mapping

from garuda.config import BoundingBox
from garuda.generators.threats import exposure_risk
from garuda.schemas.mission import GeneratedScenario

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
RISK_KEY = "processed/threat_risk.npy"
RISK_ALT_FT = 5000.0
RISK_STEP_DEG = 0.25


def minio_client():
    endpoint = os.environ.get("MINIO_ENDPOINT")
    if not endpoint:
        return None
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("MINIO_ROOT_USER", "garuda"),
        aws_secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "garuda-secret"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        config=Config(signature_version="s3v4"),
    )


def _bucket() -> str:
    return os.environ.get("MINIO_BUCKET", "garuda")


def publish_terrain(root: Path) -> int:
    from garuda.storage.gis_files import DEM_KEY, DEM_NPY, MASK_NPY, PENALTY_NPY

    client = minio_client()
    if client is None:
        return 0
    keys = (DEM_NPY, DEM_KEY, MASK_NPY, PENALTY_NPY)
    for key in keys:
        client.upload_file(str(root / key), _bucket(), key)
    return len(keys)


def threat_risk_grid(scenario: GeneratedScenario, bbox: BoundingBox) -> np.ndarray:
    lats = np.arange(bbox.lat_min, bbox.lat_max + 1e-9, RISK_STEP_DEG)
    lons = np.arange(bbox.lon_min, bbox.lon_max + 1e-9, RISK_STEP_DEG)
    active = [t for t in scenario.threats if t.status == "ACTIVE"]
    return np.array(
        [[exposure_risk(lon, lat, RISK_ALT_FT, active) for lon in lons] for lat in lats],
        dtype=np.float32,
    )


def publish_raw(scenario: GeneratedScenario, bbox: BoundingBox) -> int:
    client = minio_client()
    if client is None:
        return 0
    stamp = scenario.missions[0].window_start.astimezone(timezone.utc).strftime("%Y-%m-%d")
    uploads: list[tuple[str, bytes]] = []
    for path in sorted((FIXTURES / "weather").glob("*.json")):
        uploads.append((f"raw/weather/{stamp}/{path.name}", path.read_bytes()))
    for path in sorted((FIXTURES / "adsb").glob("*.json")):
        uploads.append((f"raw/opensky/{stamp}/{path.name}", path.read_bytes()))
    features = [
        {
            "type": "Feature",
            "properties": {"zone_id": z.zone_id, "name": z.name, "type": z.type},
            "geometry": mapping(wkt.loads(z.wkt)),
        }
        for z in scenario.zones
    ]
    collection = {"type": "FeatureCollection", "features": features}
    uploads.append(("raw/airspace/synthetic_zones.geojson", json.dumps(collection).encode()))
    buffer = io.BytesIO()
    np.save(buffer, threat_risk_grid(scenario, bbox))
    uploads.append((RISK_KEY, buffer.getvalue()))
    for key, body in uploads:
        client.put_object(Bucket=_bucket(), Key=key, Body=body)
    return len(uploads)
