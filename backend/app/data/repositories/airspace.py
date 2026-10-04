"""Bounded, parameterised spatial airspace repository; SQL is authoritative."""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any
from ...schemas.airspace import AirspaceType, AirspaceZone, ActivationWindow, AltitudeLimit, utc

def _zone(row:Any)->AirspaceZone:
    item=dict(row); item["geometry"]=json.loads(item.pop("geojson")); centerline=item.pop("centerline_json",None); item["centerline"]=json.loads(centerline) if centerline else None
    if isinstance(item.get("provenance"),str): item["provenance"]=json.loads(item["provenance"])
    item["floor"]=AltitudeLimit(value=item.pop("floor_ft"),unit="ft",datum="AMSL") if item.get("floor_ft") is not None else AltitudeLimit()
    item["ceiling"]=AltitudeLimit(value=item.pop("ceiling_ft"),unit="ft",datum="AMSL") if item.get("ceiling_ft") is not None else AltitudeLimit()
    windows=item.pop("windows",[])
    if isinstance(windows,str): windows=json.loads(windows)
    item["windows"]=tuple(ActivationWindow(start=w["active_from"],end=w["active_to"],mission_id=w["mission_id"],version=w["version"]) for w in windows); item.pop("altitude_status",None); item.pop("source_identifier",None); item.pop("last_event_id",None); item.pop("updated_at",None); item.pop("active_from",None); item.pop("active_to",None)
    return AirspaceZone.model_validate(item)

class AirspaceRepository:
    def __init__(self,pool:Any,*,statement_timeout_ms:int=3000)->None:self.pool,self.statement_timeout_ms=pool,statement_timeout_ms
    async def get_zone(self,zone_id:str)->AirspaceZone|None:
        row=await self.pool.fetchrow(self._base()+" WHERE z.zone_id=$1",zone_id); return _zone(row) if row else None
    def _base(self)->str:
        return """SELECT z.*,ST_AsGeoJSON(z.geometry) geojson,CASE WHEN z.centerline IS NULL THEN NULL ELSE ST_AsGeoJSON(z.centerline) END centerline_json,COALESCE((SELECT jsonb_agg(jsonb_build_object('active_from',w.active_from,'active_to',w.active_to,'mission_id',w.mission_id,'version',w.version) ORDER BY w.active_from) FROM airspace.zone_activation_window w WHERE w.zone_id=z.zone_id),'[]') windows FROM airspace.airspace_zone z"""
    async def list_zones(self,run_id:str,*,bbox:tuple[float,float,float,float]|None=None,types:tuple[AirspaceType,...]=(),limit:int=100,cursor:str|None=None)->dict[str,Any]:
        if not 1<=limit<=200: raise ValueError("limit must be 1..200")
        args:list[Any]=[run_id]; filters=["z.run_id=$1"]
        if cursor: args.append(cursor); filters.append(f"z.zone_id>${len(args)}")
        if types: args.append([v.value for v in types]); filters.append(f"z.type=ANY(${len(args)}::text[])")
        if bbox:
            w,s,e,n=bbox
            if not (-180<=w<e<=180 and -90<=s<n<=90): raise ValueError("invalid bbox")
            args.extend([w,s,e,n]); filters.append(f"z.geometry && ST_MakeEnvelope(${len(args)-3},${len(args)-2},${len(args)-1},${len(args)},4326)")
        args.append(limit+1); rows=await self.pool.fetch(self._base()+" WHERE "+" AND ".join(filters)+f" ORDER BY z.zone_id LIMIT ${len(args)}",*args)
        items=[_zone(row) for row in rows[:limit]]; return {"items":items,"truncated":len(rows)>limit,"cursor":items[-1].zone_id if len(rows)>limit else None,"projection":"sql"}
    async def get_active_zones(self,run_id:str,at:datetime)->list[AirspaceZone]:
        at=utc(at); rows=await self.pool.fetch(self._base()+" WHERE z.run_id=$1 AND z.enabled AND (NOT EXISTS(SELECT 1 FROM airspace.zone_activation_window w WHERE w.zone_id=z.zone_id) OR EXISTS(SELECT 1 FROM airspace.zone_activation_window w WHERE w.zone_id=z.zone_id AND w.active_from <= $2 AND w.active_to > $2)) ORDER BY z.zone_id",run_id,at); return [_zone(r) for r in rows]
    async def get_zones_intersecting_route(self,run_id:str,route:dict[str,Any],alt_ft:int,at:datetime)->list[AirspaceZone]:
        if route.get("type") not in {"LineString","MultiLineString"} or not isinstance(alt_ft,int): raise ValueError("route must be GeoJSON line and altitude integer feet")
        at=utc(at); body=json.dumps(route,separators=(",",":")); rows=await self.pool.fetch(self._base()+" WHERE z.run_id=$1 AND z.enabled AND ST_Intersects(z.geometry,ST_SetSRID(ST_GeomFromGeoJSON($2),4326)) AND (z.floor_ft IS NULL OR z.floor_ft <= $3) AND (z.ceiling_ft IS NULL OR z.ceiling_ft >= $3) AND (NOT EXISTS(SELECT 1 FROM airspace.zone_activation_window w WHERE w.zone_id=z.zone_id) OR EXISTS(SELECT 1 FROM airspace.zone_activation_window w WHERE w.zone_id=z.zone_id AND w.active_from <= $4 AND w.active_to > $4)) ORDER BY z.zone_id",run_id,body,alt_ft,at); return [_zone(r) for r in rows]
    async def get_mission_zones(self,run_id:str,mission_id:str)->list[AirspaceZone]:
        rows=await self.pool.fetch(self._base()+" JOIN airspace.mission_zone_link l ON l.zone_id=z.zone_id WHERE l.run_id=$1 AND l.mission_id=$2 ORDER BY z.zone_id",run_id,mission_id); return [_zone(r) for r in rows]
    async def check_airspace(self,run_id:str,route:dict[str,Any],alt_ft:int,at:datetime)->dict[str,Any]:
        items=await self.get_zones_intersecting_route(run_id,route,alt_ft,at)
        return {"findings":[{"zone_id":z.zone_id,"type":z.type,"uncertain":z.altitude_reason is not None,"reason":z.altitude_reason or "active footprint intersects route"} for z in items],"data_as_of":utc(at),"projection":"sql","truncated":False}
