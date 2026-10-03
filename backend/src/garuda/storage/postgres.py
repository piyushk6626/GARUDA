import asyncpg

from garuda.schemas.mission import Mission, Target


def asyncpg_dsn(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url.removeprefix("postgresql+psycopg://")
    return url


async def seed_postgres(dsn: str, targets: list[Target], missions: list[Mission]) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(dsn))
    try:
        async with connection.transaction():
            await connection.execute("TRUNCATE plan_assignment, plan, mission, target")
            await _insert_targets(connection, targets)
            await _insert_missions(connection, missions)
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
