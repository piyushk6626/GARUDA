"""Atomic authoritative writes for zones, schedule changes, and their outbox rows."""
from __future__ import annotations
import hashlib, json
from datetime import datetime
from typing import Any
from ...schemas.airspace import AirspaceEvent, AirspaceZone, utc

def canonical(value:object)->str: return json.dumps(value,sort_keys=True,separators=(",",":"),default=str)
def payload_hash(event:AirspaceEvent)->str: return hashlib.sha256(event.model_dump_json(exclude={"event_id"}).encode()).hexdigest()

class AirspaceCommandService:
    def __init__(self,pool:Any)->None:self.pool=pool
    async def upsert_zone(self,zone:AirspaceZone,*,at:datetime,idempotency_key:str="seed",kind:str="updated")->AirspaceEvent:
        at=utc(at); active=self.is_active(zone,at); event=AirspaceEvent.from_zone(zone,at=at,active=active,kind=kind); digest=payload_hash(event)
        async with self.pool.acquire() as con,con.transaction():
            duplicate=await con.fetchrow("SELECT envelope FROM airspace.airspace_outbox WHERE run_id=$1 AND zone_id=$2 AND idempotency_key=$3",zone.run_id,zone.zone_id,idempotency_key)
            if duplicate: return AirspaceEvent.model_validate_json(duplicate["envelope"]) if isinstance(duplicate["envelope"],str) else AirspaceEvent.model_validate(duplicate["envelope"])
            prior=await con.fetchrow("SELECT version FROM airspace.airspace_zone WHERE zone_id=$1 FOR UPDATE",zone.zone_id)
            if prior and prior["version"] >= zone.version: return AirspaceEvent.from_zone(zone.model_copy(update={"version":prior["version"]}),at=at,active=active,kind="idempotent")
            await con.execute("""INSERT INTO airspace.airspace_zone(zone_id,run_id,source_identifier,name,type,geometry,centerline,width_km,floor_ft,ceiling_ft,altitude_status,active_from,active_to,controlling_authority,source,capacity_per_hour,enabled,version,last_event_id,provenance,updated_at)
             VALUES($1,$2,$3,$4,$5,ST_SetSRID(ST_GeomFromGeoJSON($6),4326),CASE WHEN $7::text IS NULL THEN NULL ELSE ST_SetSRID(ST_GeomFromGeoJSON($7),4326) END,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
             ON CONFLICT(zone_id) DO UPDATE SET name=EXCLUDED.name,type=EXCLUDED.type,geometry=EXCLUDED.geometry,centerline=EXCLUDED.centerline,width_km=EXCLUDED.width_km,floor_ft=EXCLUDED.floor_ft,ceiling_ft=EXCLUDED.ceiling_ft,altitude_status=EXCLUDED.altitude_status,controlling_authority=EXCLUDED.controlling_authority,capacity_per_hour=EXCLUDED.capacity_per_hour,enabled=EXCLUDED.enabled,version=EXCLUDED.version,last_event_id=EXCLUDED.last_event_id,provenance=EXCLUDED.provenance,updated_at=EXCLUDED.updated_at WHERE airspace.airspace_zone.version < EXCLUDED.version""",
                zone.zone_id,zone.run_id,zone.provenance.get("source_identifier"),zone.name,zone.type.value,canonical(zone.geometry),canonical(zone.centerline) if zone.centerline else None,zone.width_km,zone.floor_ft,zone.ceiling_ft,zone.altitude_reason or "known",None,None,zone.controlling_authority,zone.source,zone.capacity_per_hour,zone.enabled,zone.version,event.event_id,canonical(zone.provenance),at)
            await con.execute("DELETE FROM airspace.zone_activation_window WHERE zone_id=$1",zone.zone_id)
            for window in zone.windows:
                await con.execute("INSERT INTO airspace.zone_activation_window(window_id,zone_id,active_from,active_to,mission_id,version) VALUES(gen_random_uuid(),$1,$2,$3,$4,$5)",zone.zone_id,window.start,window.end,window.mission_id,window.version)
            await con.execute("INSERT INTO airspace.airspace_event(event_id,run_id,zone_id,version,event_type,payload_hash,logical_time,envelope) VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(event_id) DO NOTHING",event.event_id,zone.run_id,zone.zone_id,zone.version,event.type,digest,at,event.model_dump_json())
            await con.execute("INSERT INTO airspace.airspace_outbox(event_id,run_id,zone_id,version,event_type,logical_time,envelope,payload_hash,idempotency_key) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT(event_id) DO NOTHING",event.event_id,zone.run_id,zone.zone_id,zone.version,event.type,at,event.model_dump_json(),digest,idempotency_key)
        return event
    @staticmethod
    def is_active(zone:AirspaceZone,at:datetime)->bool:
        return zone.enabled and (not zone.windows or any(w.start<=at<w.end for w in zone.windows))
    async def seed(self,zones:tuple[AirspaceZone,...],*,at:datetime)->int:
        for zone in zones: await self.upsert_zone(zone,at=at,idempotency_key="seed")
        return len(zones)
