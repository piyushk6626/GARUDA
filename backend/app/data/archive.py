"""Raw snapshot archive with crash-safe pending publication manifests."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable

import boto3
from botocore.config import Config


@dataclass(frozen=True)
class SnapshotManifest:
    key: str
    checksum: str
    source_id: str
    bbox: dict[str, float]
    response_time: datetime | None
    ingested_at: datetime
    schema_version: int = 1
    state: str = "pending"

    def body(self) -> bytes:
        return json.dumps({**self.__dict__, "response_time": self.response_time.isoformat() if self.response_time else None,
            "ingested_at": self.ingested_at.isoformat()}, sort_keys=True, separators=(",", ":")).encode()


class MinioSnapshotArchive:
    """Objects are immutable; the small manifest is the publication work queue."""
    def __init__(self, *, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, region_name="us-east-1", config=Config(signature_version="s3v4",
            s3={"addressing_style": "path"}, retries={"total_max_attempts": 3}))

    def initialize(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except self.client.exceptions.ClientError:
            self.client.create_bucket(Bucket=self.bucket)
        # Raw snapshots are retained separately from database facts for 30 days.
        self.client.put_bucket_lifecycle_configuration(Bucket=self.bucket,
            LifecycleConfiguration={"Rules": [{"ID": "expire-raw-30d", "Status": "Enabled",
              "Filter": {"Prefix": "snapshots/"}, "Expiration": {"Days": 30}}]})

    def archive(self, raw: bytes, *, source_id: str, bbox: dict[str, float],
                response_time: datetime | None, ingested_at: datetime) -> SnapshotManifest:
        checksum = hashlib.sha256(raw).hexdigest()
        key = f"snapshots/{source_id}/{checksum}.json"
        manifest_key = f"manifests/{checksum}.json"
        self.client.put_object(Bucket=self.bucket, Key=key, Body=raw, ContentType="application/json",
                               Metadata={"sha256": checksum, "source": source_id})
        manifest = SnapshotManifest(key=key, checksum=checksum, source_id=source_id, bbox=bbox,
            response_time=response_time, ingested_at=ingested_at)
        self.client.put_object(Bucket=self.bucket, Key=manifest_key, Body=manifest.body(),
                               ContentType="application/json", Metadata={"state": "pending"})
        return manifest

    def verify(self, manifest: SnapshotManifest) -> bytes:
        result = self.client.get_object(Bucket=self.bucket, Key=manifest.key)
        raw = result["Body"].read()
        if hashlib.sha256(raw).hexdigest() != manifest.checksum:
            raise ValueError("snapshot checksum mismatch")
        return raw

    def mark_published(self, manifest: SnapshotManifest) -> SnapshotManifest:
        done = replace(manifest, state="published")
        self.client.put_object(Bucket=self.bucket, Key=f"manifests/{manifest.checksum}.json", Body=done.body(),
                               ContentType="application/json", Metadata={"state": "published"})
        return done

    def recover_pending(self, publisher: Callable[[SnapshotManifest, bytes], None]) -> int:
        """Republish every pending manifest; publisher must be idempotent."""
        pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix="manifests/")
        recovered = 0
        for page in pages:
            for item in page.get("Contents", []):
                data = json.loads(self.client.get_object(Bucket=self.bucket, Key=item["Key"])["Body"].read())
                data["response_time"] = datetime.fromisoformat(data["response_time"]) if data["response_time"] else None
                data["ingested_at"] = datetime.fromisoformat(data["ingested_at"])
                manifest = SnapshotManifest(**data)
                if manifest.state == "pending":
                    publisher(manifest, self.verify(manifest))
                    self.mark_published(manifest)
                    recovered += 1
        return recovered
