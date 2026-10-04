import json

import asyncpg

from garuda.schemas.gis import BaseRecord, BaseTargetDistance, BorderFeature, GeoFeature
from garuda.schemas.mission import Mission, Target
from garuda.schemas.threat import Threat, ThreatTrack
from garuda.schemas.weapon import Compatibility, LoadConfig, WeaponStock, WeaponType
from garuda.schemas.weather import WeatherCell, WeatherStationObs


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
    weapons: list[WeaponType],
    stocks: list[WeaponStock],
    load_configs: list[LoadConfig],
    compatibilities: list[Compatibility],
    cells: list[WeatherCell],
    observations: list[WeatherStationObs],
    bases: list[BaseRecord],
    features: list[GeoFeature],
    borders: list[BorderFeature],
    distances: list[BaseTargetDistance],
) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(dsn))
    try:
        async with connection.transaction():
            await connection.execute(
                """
                TRUNCATE base_target_distance, plan_assignment, plan, mission, target,
                         threat_track, threat, weapon_stock, weapon_compatibility,
                         load_config, weapon_type, geo_feature, border, base
                """
            )
            await connection.execute("DELETE FROM weather_station_obs WHERE source = 'synthetic'")
            await connection.execute("DELETE FROM weather_grid WHERE source = 'synthetic'")
            await _insert_bases(connection, bases)
            await _insert_features(connection, features)
            await _insert_borders(connection, borders)
            await _insert_targets(connection, targets)
            await _insert_distances(connection, distances)
            await _insert_missions(connection, missions)
            await _insert_threats(connection, threats)
            await _insert_tracks(connection, tracks)
            await _insert_weapons(connection, weapons)
            await _insert_stocks(connection, stocks)
            await _insert_load_configs(connection, load_configs)
            await _insert_compatibilities(connection, compatibilities)
            await _upsert_weather(connection, cells, observations)
    finally:
        await connection.close()


async def upsert_gis(
    dsn: str,
    bases: list[BaseRecord],
    features: list[GeoFeature],
    borders: list[BorderFeature],
) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(dsn))
    try:
        async with connection.transaction():
            await _upsert_bases(connection, bases)
            await connection.execute("DELETE FROM geo_feature")
            await connection.execute("DELETE FROM border")
            await _insert_features(connection, features)
            await _insert_borders(connection, borders)
    finally:
        await connection.close()


async def upsert_weather(
    dsn: str,
    cells: list[WeatherCell],
    observations: list[WeatherStationObs],
) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(dsn))
    try:
        async with connection.transaction():
            await _upsert_weather(connection, cells, observations)
    finally:
        await connection.close()


async def _insert_bases(connection: asyncpg.Connection, bases: list[BaseRecord]) -> None:
    await _upsert_bases(connection, bases)


async def _upsert_bases(connection: asyncpg.Connection, bases: list[BaseRecord]) -> None:
    if not bases:
        return
    await connection.executemany(
        """
        INSERT INTO base (
            base_id, name, icao, position, elevation_ft, runway_count, runway_length_m,
            shelter_count, fuel_capacity_l, ground_crew_teams, max_sorties_per_hour
        )
        VALUES (
            $1, $2, $3, ST_SetSRID(ST_MakePoint($4, $5), 4326), $6, $7, $8, $9, $10, $11, $12
        )
        ON CONFLICT (base_id) DO UPDATE SET
            name = EXCLUDED.name,
            icao = EXCLUDED.icao,
            position = EXCLUDED.position,
            elevation_ft = EXCLUDED.elevation_ft,
            runway_count = EXCLUDED.runway_count,
            runway_length_m = EXCLUDED.runway_length_m,
            shelter_count = EXCLUDED.shelter_count,
            fuel_capacity_l = EXCLUDED.fuel_capacity_l,
            ground_crew_teams = EXCLUDED.ground_crew_teams,
            max_sorties_per_hour = EXCLUDED.max_sorties_per_hour
        """,
        [_base_row(base) for base in bases],
    )


async def _insert_features(connection: asyncpg.Connection, features: list[GeoFeature]) -> None:
    if not features:
        return
    await connection.executemany(
        """
        INSERT INTO geo_feature (feature_id, kind, name, geom)
        VALUES ($1, $2, $3, ST_GeomFromText($4, 4326))
        """,
        [(feature.feature_id, feature.kind, feature.name, feature.wkt) for feature in features],
    )


async def _insert_borders(connection: asyncpg.Connection, borders: list[BorderFeature]) -> None:
    if not borders:
        return
    await connection.executemany(
        """
        INSERT INTO border (border_id, name, kind, geom)
        VALUES ($1, $2, $3, ST_GeomFromText($4, 4326))
        """,
        [(border.border_id, border.name, border.kind, border.wkt) for border in borders],
    )


async def _insert_distances(
    connection: asyncpg.Connection,
    distances: list[BaseTargetDistance],
) -> None:
    if not distances:
        return
    await connection.executemany(
        """
        INSERT INTO base_target_distance (base_id, target_id, distance_km)
        VALUES ($1, $2, $3)
        """,
        [(row.base_id, row.target_id, row.distance_km) for row in distances],
    )


def _base_row(base: BaseRecord) -> tuple[object, ...]:
    return (
        base.base_id,
        base.name,
        base.icao,
        base.lon,
        base.lat,
        base.elevation_ft,
        base.runway_count,
        base.runway_length_m,
        base.shelter_count,
        base.fuel_capacity_l,
        base.ground_crew_teams,
        base.max_sorties_per_hour,
    )


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


async def _insert_weapons(connection: asyncpg.Connection, weapons: list[WeaponType]) -> None:
    await connection.executemany(
        """
        INSERT INTO weapon_type (
            weapon_code, category, range_km, weight_kg, pk_by_target_class,
            min_release_alt_ft, guidance
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
        """,
        [
            (
                weapon.weapon_code,
                weapon.category,
                weapon.range_km,
                weapon.weight_kg,
                json.dumps(weapon.pk_by_target_class),
                weapon.min_release_alt_ft,
                weapon.guidance,
            )
            for weapon in weapons
        ],
    )


async def _insert_stocks(connection: asyncpg.Connection, stocks: list[WeaponStock]) -> None:
    await connection.executemany(
        """
        INSERT INTO weapon_stock (base_id, weapon_code, quantity, reserved_qty, updated_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        [
            (
                stock.base_id,
                stock.weapon_code,
                stock.quantity,
                stock.reserved_qty,
                stock.updated_at,
            )
            for stock in stocks
        ],
    )


async def _insert_load_configs(connection: asyncpg.Connection, load_configs: list[LoadConfig]) -> None:
    await connection.executemany(
        """
        INSERT INTO load_config (
            config_id, aircraft_type, role, stations, total_weight_kg, range_penalty_pct
        )
        VALUES ($1, $2, $3, $4::jsonb, $5, $6)
        """,
        [
            (
                config.config_id,
                config.aircraft_type,
                config.role,
                json.dumps(config.stations),
                config.total_weight_kg,
                config.range_penalty_pct,
            )
            for config in load_configs
        ],
    )


async def _upsert_weather(
    connection: asyncpg.Connection,
    cells: list[WeatherCell],
    observations: list[WeatherStationObs],
) -> None:
    if cells:
        await connection.executemany(
            """
            INSERT INTO weather_grid (
                time, lat, lon, position,
                cloud_low_pct, cloud_mid_pct, cloud_high_pct, ceiling_ft,
                visibility_km, wind_kts, wind_dir_deg, gust_kts,
                precip_mm_h, cape_jkg, temp_c, freezing_level_ft,
                source, issued_at, flyability, cb_flag
            )
            VALUES (
                $1, $2, $3, ST_SetSRID(ST_MakePoint($3, $2), 4326),
                $4, $5, $6, $7,
                $8, $9, $10, $11,
                $12, $13, $14, $15,
                $16, $17, $18, $19
            )
            ON CONFLICT (time, lat, lon, source) DO UPDATE SET
                position = EXCLUDED.position,
                cloud_low_pct = EXCLUDED.cloud_low_pct,
                cloud_mid_pct = EXCLUDED.cloud_mid_pct,
                cloud_high_pct = EXCLUDED.cloud_high_pct,
                ceiling_ft = EXCLUDED.ceiling_ft,
                visibility_km = EXCLUDED.visibility_km,
                wind_kts = EXCLUDED.wind_kts,
                wind_dir_deg = EXCLUDED.wind_dir_deg,
                gust_kts = EXCLUDED.gust_kts,
                precip_mm_h = EXCLUDED.precip_mm_h,
                cape_jkg = EXCLUDED.cape_jkg,
                temp_c = EXCLUDED.temp_c,
                freezing_level_ft = EXCLUDED.freezing_level_ft,
                issued_at = EXCLUDED.issued_at,
                flyability = EXCLUDED.flyability,
                cb_flag = EXCLUDED.cb_flag
            """,
            [
                (
                    cell.time,
                    cell.lat,
                    cell.lon,
                    cell.cloud_low_pct,
                    cell.cloud_mid_pct,
                    cell.cloud_high_pct,
                    cell.ceiling_ft,
                    cell.visibility_km,
                    cell.wind_kts,
                    cell.wind_dir_deg,
                    cell.gust_kts,
                    cell.precip_mm_h,
                    cell.cape_jkg,
                    cell.temp_c,
                    cell.freezing_level_ft,
                    cell.source,
                    cell.issued_at,
                    cell.flyability,
                    cell.cb_flag,
                )
                for cell in cells
            ],
        )
    if observations:
        await connection.executemany(
            """
            INSERT INTO weather_station_obs (
                obs_time, icao, source, raw_text, flight_category,
                ceiling_ft, vis_km, wind_kts, crosswind_kts, flyability
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (obs_time, icao, source) DO UPDATE SET
                raw_text = EXCLUDED.raw_text,
                flight_category = EXCLUDED.flight_category,
                ceiling_ft = EXCLUDED.ceiling_ft,
                vis_km = EXCLUDED.vis_km,
                wind_kts = EXCLUDED.wind_kts,
                crosswind_kts = EXCLUDED.crosswind_kts,
                flyability = EXCLUDED.flyability
            """,
            [
                (
                    obs.obs_time,
                    obs.icao,
                    obs.source,
                    obs.raw_text,
                    obs.flight_category,
                    obs.ceiling_ft,
                    obs.vis_km,
                    obs.wind_kts,
                    obs.crosswind_kts,
                    obs.flyability,
                )
                for obs in observations
            ],
        )


async def _insert_compatibilities(
    connection: asyncpg.Connection,
    compatibilities: list[Compatibility],
) -> None:
    await connection.executemany(
        """
        INSERT INTO weapon_compatibility (aircraft_type, weapon_code, allowed_stations)
        VALUES ($1, $2, $3)
        """,
        [
            (row.aircraft_type, row.weapon_code, row.allowed_stations)
            for row in compatibilities
        ],
    )
