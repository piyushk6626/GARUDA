"""Table-driven Postgres writer: one spec per table, one generic insert loop."""

import json
from collections.abc import Callable
from typing import Any

import asyncpg

from garuda.schemas.mission import GeneratedScenario

Attr = str | Callable[[Any], Any]
Column = tuple[str, tuple[Attr, ...], str]

TRUNCATE_ALL = (
    "adsb_track, crew_duty_log, crew, aircraft_status_history, aircraft, squadron, aircraft_type, "
    "airspace_zone, base_target_distance, plan_assignment, plan, mission, target, threat_track, "
    "threat, weapon_stock, weapon_compatibility, load_config, weapon_type, geo_feature, border, base"
)

POINT = "ST_SetSRID(ST_MakePoint({}, {}), 4326)"
WKT = "ST_GeomFromText({}, 4326)"
JSONB = "{}::jsonb"


def col(name: str, *attrs: Attr, sql: str = "{}") -> Column:
    return (name, attrs or (name,), sql)


def jsonb(attr: str) -> Callable[[Any], str]:
    return lambda row: json.dumps(getattr(row, attr))


class Table:
    def __init__(self, name: str, attr: str, columns: list[Column], key: tuple[str, ...] = ()) -> None:
        self.name = name
        self.attr = attr
        self.columns = columns
        self.key = key

    def sql(self) -> str:
        names = ", ".join(name for name, _, _ in self.columns)
        values, index = [], 1
        for _, attrs, template in self.columns:
            values.append(template.format(*(f"${index + offset}" for offset in range(len(attrs)))))
            index += len(attrs)
        statement = f"INSERT INTO {self.name} ({names}) VALUES ({', '.join(values)})"
        if not self.key:
            return statement
        updates = ", ".join(
            f"{name} = EXCLUDED.{name}" for name, _, _ in self.columns if name not in self.key
        )
        return f"{statement} ON CONFLICT ({', '.join(self.key)}) DO UPDATE SET {updates}"

    def rows(self, items: list[Any]) -> list[tuple[Any, ...]]:
        flat = [attr for _, attrs, _ in self.columns for attr in attrs]
        return [
            tuple(attr(item) if callable(attr) else getattr(item, attr) for attr in flat)
            for item in items
        ]


BASE = Table("base", "bases", [
    col("base_id"), col("name"), col("icao"), col("position", "lon", "lat", sql=POINT),
    col("elevation_ft"), col("runway_count"), col("runway_length_m"), col("shelter_count"),
    col("fuel_capacity_l"), col("ground_crew_teams"), col("max_sorties_per_hour"),
], key=("base_id",))
FEATURE = Table("geo_feature", "features", [
    col("feature_id"), col("kind"), col("name"), col("geom", "wkt", sql=WKT),
])
BORDER = Table("border", "borders", [
    col("border_id"), col("name"), col("kind"), col("geom", "wkt", sql=WKT),
])
WEATHER_GRID = Table("weather_grid", "cells", [
    col("time"), col("lat"), col("lon"), col("position", "lon", "lat", sql=POINT),
    col("cloud_low_pct"), col("cloud_mid_pct"), col("cloud_high_pct"), col("ceiling_ft"),
    col("visibility_km"), col("wind_kts"), col("wind_dir_deg"), col("gust_kts"),
    col("precip_mm_h"), col("cape_jkg"), col("temp_c"), col("freezing_level_ft"),
    col("source"), col("issued_at"), col("flyability"), col("cb_flag"),
], key=("time", "lat", "lon", "source"))
WEATHER_OBS = Table("weather_station_obs", "observations", [
    col("obs_time"), col("icao"), col("source"), col("raw_text"), col("flight_category"),
    col("ceiling_ft"), col("vis_km"), col("wind_kts"), col("crosswind_kts"), col("flyability"),
], key=("obs_time", "icao", "source"))

# Insert order matters: parents before children.
SEED_TABLES: list[Table] = [
    BASE, FEATURE, BORDER,
    Table("target", "targets", [
        col("target_id"), col("class", "target_class"), col("position", "lon", "lat", sql=POINT),
        col("value"), col("hardness"), col("defended_by"),
    ]),
    Table("base_target_distance", "distances", [col("base_id"), col("target_id"), col("distance_km")]),
    Table("mission", "missions", [
        col("mission_id"), col("type"), col("priority"), col("value"), col("requested_by"),
        col("window_start"), col("window_end"), col("duration_min"), col("target_id"),
        col("area", "area_wkt", sql=WKT), col("required_capabilities"), col("min_aircraft"),
        col("max_aircraft"), col("required_effect"), col("min_pk"), col("status"),
        col("dependencies"),
    ]),
    Table("threat", "threats", [
        col("threat_id"), col("type"), col("position", "lon", "lat", sql=POINT), col("mobile"),
        col("engagement_range_km"), col("detection_range_km"), col("min_alt_ft"),
        col("max_alt_ft"), col("pk_base"), col("confidence"), col("first_seen"),
        col("last_seen"), col("status"),
    ]),
    Table("threat_track", "tracks", [
        col("time"), col("threat_id"), col("geom", "lon", "lat", sql=POINT),
        col("speed_kts"), col("heading"),
    ]),
    Table("weapon_type", "weapons", [
        col("weapon_code"), col("category"), col("range_km"), col("weight_kg"),
        col("pk_by_target_class", jsonb("pk_by_target_class"), sql=JSONB),
        col("min_release_alt_ft"), col("guidance"),
    ]),
    Table("weapon_stock", "stocks", [
        col("base_id"), col("weapon_code"), col("quantity"), col("reserved_qty"), col("updated_at"),
    ]),
    Table("load_config", "load_configs", [
        col("config_id"), col("aircraft_type"), col("role"),
        col("stations", jsonb("stations"), sql=JSONB),
        col("total_weight_kg"), col("range_penalty_pct"),
    ]),
    Table("weapon_compatibility", "compatibilities", [
        col("aircraft_type"), col("weapon_code"), col("allowed_stations"),
    ]),
    WEATHER_GRID, WEATHER_OBS,
    Table("aircraft_type", "aircraft_types", [
        col("type"), col("fuel_capacity_kg"), col("inspection_cycle_hours"),
        col("turnaround_min"), col("range_km"), col("cruise_kts"), col("ceiling_ft"),
        col("capabilities"),
    ]),
    Table("squadron", "squadrons", [col("sqn_id"), col("type"), col("home_base_id"), col("name")]),
    Table("aircraft", "aircraft", [
        col("tail_no"), col("type"), col("sqn_id"), col("base_id"), col("status"),
        col("status_reason"), col("fuel_kg"), col("fuel_capacity_kg"), col("flight_hours_total"),
        col("hours_to_next_inspection"), col("inspection_cycle_hours"), col("maint_return_eta"),
        col("turnaround_min"), col("capabilities"), col("range_km"), col("cruise_kts"),
        col("ceiling_ft"), col("updated_at"),
    ]),
    Table("aircraft_status_history", "aircraft_history", [
        col("time"), col("tail_no"), col("status"), col("base_id"), col("fuel_kg"),
        col("flight_hours_total"), col("hours_to_next_inspection"),
    ]),
    Table("crew", "crew", [
        col("crew_id"), col("callsign"), col("role"), col("sqn_id"), col("base_id"),
        col("primary_type"), col("qualifications", jsonb("qualifications"), sql=JSONB),
        col("currency", lambda m: json.dumps(m.currency.model_dump(mode="json")), sql=JSONB),
        col("flight_hours_total"), col("flight_hours_last_30d"), col("duty_hours_24h"),
        col("rest_hours_since_last_duty"), col("fatigue_score"), col("medical_status"),
        col("leave_until"), col("current_assignment"), col("is_flight_lead"), col("updated_at"),
    ]),
    Table("crew_duty_log", "crew_duty", [
        col("time"), col("crew_id"), col("duty_hours_24h"), col("fatigue_score"),
    ]),
    Table("airspace_zone", "zones", [
        col("zone_id"), col("name"), col("type"), col("geometry", "wkt", sql=WKT),
        col("floor_ft"), col("ceiling_ft"), col("active_from"), col("active_to"),
        col("controlling_authority"), col("source"), col("capacity_per_hour"),
        col("width_km"), col("mission_id"),
    ]),
    Table("adsb_track", "adsb_tracks", [
        col("time"), col("source_id"), col("icao24"), col("callsign"), col("origin_country"),
        col("last_contact"), col("lat"), col("lon"), col("geom", "lon", "lat", sql=POINT),
        col("alt_m"), col("geo_alt_m"), col("speed_ms"), col("heading"), col("vrate"),
        col("on_ground"), col("position_source"),
    ]),
]


def asyncpg_dsn(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url.removeprefix("postgresql+psycopg://")
    return url


async def _write(connection: asyncpg.Connection, table: Table, items: list[Any]) -> None:
    if items:
        await connection.executemany(table.sql(), table.rows(items))


async def seed_postgres(dsn: str, scenario: GeneratedScenario) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(dsn))
    try:
        async with connection.transaction():
            await connection.execute(f"TRUNCATE {TRUNCATE_ALL}")
            await connection.execute("DELETE FROM weather_station_obs WHERE source = 'synthetic'")
            await connection.execute("DELETE FROM weather_grid WHERE source = 'synthetic'")
            for table in SEED_TABLES:
                await _write(connection, table, getattr(scenario, table.attr))
    finally:
        await connection.close()


async def upsert_gis(dsn: str, bases: list, features: list, borders: list) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(dsn))
    try:
        async with connection.transaction():
            await _write(connection, BASE, bases)
            await connection.execute("DELETE FROM geo_feature")
            await connection.execute("DELETE FROM border")
            await _write(connection, FEATURE, features)
            await _write(connection, BORDER, borders)
    finally:
        await connection.close()


async def upsert_weather(dsn: str, cells: list, observations: list) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(dsn))
    try:
        async with connection.transaction():
            await _write(connection, WEATHER_GRID, cells)
            await _write(connection, WEATHER_OBS, observations)
    finally:
        await connection.close()
