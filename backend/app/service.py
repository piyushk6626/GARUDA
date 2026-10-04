"""Small composition root shared by the poller, replay command, and workers."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import AdsBSettings
from .data.archive import MinioSnapshotArchive
from .schemas.adsb import normalize_opensky_response


class IngestionService:
    def __init__(self, settings: AdsBSettings, source, archive: MinioSnapshotArchive, publisher) -> None:
        self.settings, self.source, self.archive, self.publisher = settings, source, archive, publisher
        self.metrics = {"validated_rows": 0, "dropped_rows": 0, "publish_failures": 0, "pending_archives": 0}

    async def poll_once(self) -> int:
        document = await self.source.fetch_states()
        now = datetime.now(timezone.utc)
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        manifest = self.archive.archive(raw, source_id=self.settings.source_id,
            bbox=self.settings.bounding_box.query_params(), response_time=None, ingested_at=now)
        normalized = normalize_opensky_response(document, source_id=self.settings.source_id,
            ingested_at=now, snapshot_key=manifest.key)
        self.metrics["validated_rows"] += len(normalized.observations)
        self.metrics["dropped_rows"] += normalized.malformed_rows + normalized.excluded_rows
        try:
            await self.publisher.publish_many(item.to_event() for item in normalized.observations)
        except Exception:
            self.metrics["publish_failures"] += 1
            self.metrics["pending_archives"] += 1
            raise
        self.archive.mark_published(manifest)
        return len(normalized.observations)
