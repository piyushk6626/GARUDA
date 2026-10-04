"""Deterministic metric airspace geometry.  This module deliberately has no I/O."""
from __future__ import annotations
import hashlib, json, uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import numpy as np
from pyproj import Transformer
from shapely import box, from_geojson, to_geojson
from shapely.geometry import LineString, shape
from shapely.ops import transform
from ...airspace_config import AirspaceScenario
from ...schemas.airspace import ActivationWindow, AirspaceType, AltitudeLimit, AirspaceZone

def _json(geometry: object) -> dict:
    """Pin GeoJSON precision so equivalent GEOS/PROJ builds produce the same seed."""
    def rounded(value):
        if isinstance(value,float): return round(value,7)
        if isinstance(value,list): return [rounded(item) for item in value]
        if isinstance(value,dict): return {key:rounded(item) for key,item in value.items()}
        return value
    return rounded(json.loads(to_geojson(geometry, indent=None)))
def _transformer(source:str,target:str): return Transformer.from_crs(source,target,always_xy=True).transform

@dataclass(frozen=True)
class AirspaceSeed:
    zones: tuple[AirspaceZone,...]
    def checksum(self)->str:
        return hashlib.sha256(json.dumps([z.model_dump(mode="json") for z in self.zones],sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

def _stable_id(run_id:str, name:str)->str:
    return f"{run_id}:synthetic:{uuid.uuid5(uuid.NAMESPACE_URL,run_id+':'+name).hex[:16]}"

def _metric_context(s:AirspaceScenario):
    west,south,east,north=s.theatre.bbox
    theatre_ll=box(west,south,east,north)
    return transform(_transformer("EPSG:4326",s.theatre.metric_crs),theatre_ll), _transformer(s.theatre.metric_crs,"EPSG:4326")

def _footprint(metric, theatre, to_ll):
    clipped=metric.intersection(theatre)
    if clipped.is_empty: raise ValueError("generated geometry was outside configured theatre")
    if clipped.geom_type == "GeometryCollection":
        polygons=[g for g in clipped.geoms if g.geom_type in {"Polygon","MultiPolygon"}]
        if not polygons: raise ValueError("clip produced no polygon")
        clipped=polygons[0]
    if clipped.geom_type not in {"Polygon","MultiPolygon"}: raise ValueError("footprint must be polygonal")
    return _json(transform(to_ll,clipped))

def generate_airspace(scenario:AirspaceScenario,run_id:str,*, border_path:str|Path|None=None)->AirspaceSeed:
    """Build 8 CAPs, 3 AAR tracks, 4 corridors, TRAs, ADIZ and MEZ deterministically."""
    rng=np.random.default_rng(np.random.SeedSequence(scenario.seed,spawn_key=(7,)))
    theatre,to_ll=_metric_context(scenario); minx,miny,maxx,maxy=theatre.bounds
    result:list[AirspaceZone]=[]
    floor=AltitudeLimit(value=5000,unit="ft",datum="AMSL"); ceiling=AltitudeLimit(value=30000,unit="ft",datum="AMSL")
    def zone(name,kind,geom,**kwargs):
        provenance={"generator":"airspace","generator_version":scenario.generator_version,"seed":scenario.seed,"metric_crs":scenario.theatre.metric_crs}
        provenance.update(kwargs.pop("provenance",{}))
        result.append(AirspaceZone(zone_id=_stable_id(run_id,name),run_id=run_id,name=name,type=kind,
          geometry=_footprint(geom,theatre,to_ll),floor=kwargs.pop("floor",floor),ceiling=kwargs.pop("ceiling",ceiling),
          controlling_authority="IAF synthetic",source="synthetic",provenance=provenance,**kwargs))
    # evenly distributed candidates are stable and bounded; each nominal CAP is 80 x 40 km.
    xs=np.linspace(minx+70_000,maxx-70_000,4); ys=np.linspace(miny+45_000,maxy-45_000,2)
    for i,(x,y) in enumerate((p for p in ((x,y) for y in ys for x in xs)),1):
        zone(f"CAP-{i:02d}",AirspaceType.CAP,box(x-40_000,y-20_000,x+40_000,y+20_000),capacity_per_hour=2)
    for i in range(3):
        x=float(rng.uniform(minx+170_000,maxx-170_000)); y=float(rng.uniform(miny+80_000,maxy-80_000))
        line=LineString([(x-100_000,y),(x+100_000,y)])
        zone(f"AAR-{i+1:02d}",AirspaceType.AAR_TRACK,line.buffer(10_000),width_km=20,centerline=_json(transform(to_ll,line)),capacity_per_hour=1)
    for i in range(4):
        x=float(rng.uniform(minx+90_000,maxx-90_000)); y=float(rng.uniform(miny+90_000,maxy-90_000)); line=LineString([(x-55_000,y-25_000),(x+55_000,y+25_000)])
        zone(f"CORRIDOR-{i+1:02d}",AirspaceType.CORRIDOR,line.buffer(10_000),width_km=20,centerline=_json(transform(to_ll,line)),capacity_per_hour=4)
    for i,window in enumerate(scenario.tra_windows,1):
        x=minx+(i/(len(scenario.tra_windows)+1))*(maxx-minx); y=(miny+maxy)/2
        zone(f"TRA-{i:02d}",AirspaceType.TRA,box(x-25_000,y-25_000,x+25_000,y+25_000),windows=(window,),capacity_per_hour=0)
    path=Path(border_path or scenario.border_file); raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=scenario.border_sha256: raise ValueError("pinned border checksum differs from scenario")
    border=shape(json.loads(raw)["features"][0]["geometry"]); border_m=transform(_transformer("EPSG:4326",scenario.theatre.metric_crs),border)
    zone("ADIZ-SYNTHETIC",AirspaceType.ADIZ,border_m.buffer(scenario.adiz_buffer_km*1000),floor=AltitudeLimit(value=0,unit="ft",datum="AMSL"),ceiling=AltitudeLimit(value=60000,unit="ft",datum="AMSL"),capacity_per_hour=None)
    for i in range(2):
        x=minx+(i+1)*(maxx-minx)/3; y=miny+(i+1)*(maxy-miny)/3
        zone(f"MEZ-{i+1:02d}",AirspaceType.RESTRICTED,box(x-35_000,y-35_000,x+35_000,y+35_000),capacity_per_hour=0,provenance={"subtype":"MISSILE_ENGAGEMENT","generator":"airspace","seed":scenario.seed,"metric_crs":scenario.theatre.metric_crs})
    return AirspaceSeed(tuple(sorted(result,key=lambda item:item.zone_id)))
