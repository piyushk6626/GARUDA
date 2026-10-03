import asyncpg

from garuda.schemas.mission import Mission, Target
from garuda.schemas.threat import Threat, ThreatTrack


def asyncpg_dsn(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url.removeprefix("postgresql+psycopg://")
    return url


async def seed_postgres(
    dsn: str,
    targets: list[Target],
    missions: list[Mission],
    threats: list[Threat],
    tracks: list[ThreatTrack],
) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(dsn))
    try:
        async with connection.transaction():
            await connection.execute(
                "TRUNCATE plan_assignment, plan, mission, target, threat_track, threat"
            )
            await _insert_targets(connection, targets)
            await _insert_missions(connection, missions)
            await _insert_threats(connection, threats)
            await _insert_tracks(connection, tracks)
    finally:
        await connection.close()


async def _insert_targets(connection: asyncpg.Connection, targets: list[Target]) -> None:
    await connection.executemany(
        """
        INSERT INTO target (target_id, class, position, value, hardness, defended_by)
        VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326), $5, $6, $7)
        """,
        [
            (
                target.target_id,
                target.target_class,
                target.lon,
                target.lat,
                target.value,
                target.hardness,
                target.defended_by,
            )
            for target in targets
        ],
    )


async def _insert_missions(connection: asyncpg.Connection, missions: list[Mission]) -> None:
    await connection.executemany(
        """
        INSERT INTO mission (
            mission_id, type, priority, value, requested_by,
            window_start, window_end, duration_min, target_id, area,
            required_capabilities, min_aircraft, max_aircraft,
            required_effect, min_pk, status, dependencies
        )
        VALUES (
            $1, $2, $3, $4, $5,
            $6, $7, $8, $9, ST_GeomFromText($10, 4326),
            $11, $12, $13,
            $14, $15, $16, $17
        )
        """,
        [
            (
                mission.mission_id,
                mission.type,
                mission.priority,
                mission.value,
                mission.requested_by,
                mission.window_start,
                mission.window_end,
                mission.duration_min,
                mission.target_id,
                mission.area_wkt,
                mission.required_capabilities,
                mission.min_aircraft,
                mission.max_aircraft,
                mission.required_effect,
                mission.min_pk,
                mission.status,
                mission.dependencies,
            )
            for mission in missions
        ],
    )


async def _insert_threats(connection: asyncpg.Connection, threats: list[Threat]) -> None:
    await connection.executemany(
        """
        INSERT INTO threat (
            threat_id, type, position, mobile, engagement_range_km, detection_range_km,
            min_alt_ft, max_alt_ft, pk_base, confidence, first_seen, last_seen, status
        )
        VALUES (
            $1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326), $5, $6, $7,
            $8, $9, $10, $11, $12, $13, $14
        )
        """,
        [
            (
                threat.threat_id,
                threat.type,
                threat.lon,
                threat.lat,
                threat.mobile,
                threat.engagement_range_km,
                threat.detection_range_km,
                threat.min_alt_ft,
                threat.max_alt_ft,
                threat.pk_base,
                threat.confidence,
                threat.first_seen,
                threat.last_seen,
                threat.status,
            )
            for threat in threats
        ],
    )


async def _insert_tracks(connection: asyncpg.Connection, tracks: list[ThreatTrack]) -> None:
    await connection.executemany(
        """
        INSERT INTO threat_track (time, threat_id, geom, speed_kts, heading)
        VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326), $5, $6)
        """,
        [
            (
                track.time,
                track.threat_id,
                track.lon,
                track.lat,
                track.speed_kts,
                track.heading,
            )
            for track in tracks
        ],
    )
