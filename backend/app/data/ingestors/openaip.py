"""Offline-safe OpenAIP normalisation. Live network use is disabled until contract validation."""
from __future__ import annotations
import hashlib, json
from datetime import datetime
from typing import Any
from ...schemas.airspace import AirspaceType, AirspaceZone, AltitudeLimit

TYPE_MAP={"PROHIBITED":AirspaceType.PROHIBITED,"RESTRICTED":AirspaceType.RESTRICTED,"DANGER":AirspaceType.DANGER,"CTR":AirspaceType.CTR,"TMA":AirspaceType.TMA}
class OpenAIPContractError(RuntimeError): pass
def live_contract(*_:Any,**__:Any)->None:
    raise OpenAIPContractError("live OpenAIP ingestion is disabled: authenticate, pagination, category and altitude contract validation has not been recorded")
def fabricated_zones(raw:bytes,run_id:str)->tuple[AirspaceZone,...]:
    """Read the checked-in fabricated FeatureCollection, never mistaking it for provider data."""
    data=json.loads(raw); features=data.get("features",[]); out=[]
    for feature in features:
        p=feature.get("properties",{}); geometry=feature.get("geometry",{})
        kind=TYPE_MAP.get(str(p.get("category","")).upper())
        if not kind: continue
        source_id=str(p.get("id",p.get("name","unknown")))
        out.append(AirspaceZone(zone_id=f"{run_id}:fabricated:{source_id}",run_id=run_id,name=str(p.get("name",source_id)),type=kind,geometry=geometry,
          floor=AltitudeLimit(value=p.get("floor_ft"),unit="ft",datum="AMSL") if p.get("floor_ft") is not None else AltitudeLimit(),
          ceiling=AltitudeLimit(value=p.get("ceiling_ft"),unit="ft",datum="AMSL") if p.get("ceiling_ft") is not None else AltitudeLimit(),controlling_authority=str(p.get("authority","fabricated civil example")),source="fabricated",capacity_per_hour=p.get("capacity_per_hour"),provenance={"source_identifier":source_id,"fabricated":True,"raw_sha256":hashlib.sha256(raw).hexdigest(),"altitude_status":"unknown" if p.get("floor_ft") is None else "known"}))
    return tuple(sorted(out,key=lambda z:z.zone_id))
