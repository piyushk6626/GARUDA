"""Typed, bounded and parameterized ADS-B query interface."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from ...schemas.adsb import PositionObservation


class TrafficQuery(BaseModel):
    polygon_geojson: dict[str, Any]
    since: datetime
    until: datetime
    source_id: str
    limit: int = Field(default=100, ge=1, le=1000)


class TrafficRepository:
    """The pool must be created using `garuda_adsb_reader`, never the writer role."""
    def __init__(self, pool, cache, *, freshness_seconds: int = 120) -> None:
        self.pool, self.cache, self.freshness = pool, cache, timedelta(seconds=freshness_seconds)

    async def get_live(self, polygon: dict[str, Any], *, as_of: datetime, limit: int = 100) -> list[PositionObservation]:
        # Redis filters freshness; PostGIS exact polygon filtering avoids GEO radius false positives.
        query = """SELECT time,source_id,icao24,callsign,origin_country,last_contact,lat,lon,alt_m,geo_alt_m,
        speed_ms,heading,vrate,on_ground,position_source,ingested_at,snapshot_key
        FROM adsb.adsb_track WHERE time >= $1 AND time <= $2
        AND ST_Contains(ST_SetSRID(ST_GeomFromGeoJSON($3),4326), geom)
        ORDER BY time DESC, icao24 LIMIT $4"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, as_of-self.freshness, as_of, __import__('json').dumps(polygon), limit)
        items = [PositionObservation.model_validate(dict(row)) for row in rows]
        return [item for item in items if await self.cache.get(item.icao24, now=as_of)]

    async def get_history(self, polygon: dict[str, Any], *, since: datetime, until: datetime,
                          source_id: str, limit: int = 100, cursor: tuple[datetime, str] | None = None) -> list[PositionObservation]:
        since = max(since, datetime.now(timezone.utc) - timedelta(hours=24))
        cursor_time, cursor_icao = cursor or (datetime.max.replace(tzinfo=timezone.utc), "~~~~~~")
        query = """SELECT time,source_id,icao24,callsign,origin_country,last_contact,lat,lon,alt_m,geo_alt_m,
          speed_ms,heading,vrate,on_ground,position_source,ingested_at,snapshot_key FROM adsb.adsb_track
          WHERE source_id=$1 AND time >= $2 AND time <= $3 AND (time,icao24) < ($4,$5)
          AND ST_Contains(ST_SetSRID(ST_GeomFromGeoJSON($6),4326),geom)
          ORDER BY time DESC,icao24 DESC LIMIT $7"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, source_id, since, until, cursor_time, cursor_icao, __import__('json').dumps(polygon), limit)
        return [PositionObservation.model_validate(dict(row)) for row in rows]

    async def get_density(self, polygon: dict[str, Any], *, since: datetime, until: datetime, source_id: str) -> list[dict[str, Any]]:
        query = """SELECT hour,cell_id,count(*) AS aircraft_count,0.05 AS cell_degrees
          FROM adsb.grid_occupancy WHERE source_id=$1 AND hour >= $2 AND hour <= $3
          GROUP BY hour,cell_id ORDER BY hour,cell_id"""
        async with self.pool.acquire() as conn: return [dict(row) for row in await conn.fetch(query, source_id, since, until)]

    async def get_civil_traffic(self, query: TrafficQuery) -> dict[str, Any]:
        # 'civil' describes the consumer's deconfliction scope; observation itself is not identity proof.
        items = await self.get_history(query.polygon_geojson, since=query.since, until=query.until,
            source_id=query.source_id, limit=query.limit + 1)
        return {"items": [item.model_dump(mode="json") for item in items[:query.limit]],
            "data_as_of": datetime.now(timezone.utc).isoformat(), "source_id": query.source_id,
            "coverage": "observed; coverage may be sparse and does not prove empty airspace",
            "truncated": len(items) > query.limit}
