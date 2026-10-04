"""Bounded PostgreSQL query adapter for availability consumers."""
from __future__ import annotations
from datetime import datetime
from typing import Any
from ...schemas.aircraft import AircraftState, AircraftStatus, AvailabilityQuery

def _state(row: Any) -> AircraftState:
    item=dict(row); item.pop("recorded_at",None); item["capabilities"]=tuple(item["capabilities"]); return AircraftState.model_validate(item)

class AircraftRepository:
    def __init__(self,pool:Any,*,cache:Any|None=None,graph:Any|None=None)->None:self.pool,self.cache,self.graph=pool,cache,graph
    async def get_aircraft_availability(self,query:AvailabilityQuery,run_id:str,as_of:datetime|None=None)->dict[str,Any]:
        if as_of is not None:
            rows=await self.pool.fetch("""SELECT DISTINCT ON (h.tail_no) h.tail_no,h.run_id,a.type,a.sqn_id,h.base_id,h.status,h.status_reason,h.fuel_kg,h.flight_hours_total,h.hours_since_inspection,h.hours_to_next_inspection,h.maint_return_eta,h.capabilities,a.turnaround_min,h.status_since,h.ready_at,h.time AS updated_at,h.version,h.event_id AS last_event_id FROM aircraft.aircraft_status_history h JOIN aircraft.aircraft a USING(tail_no) WHERE h.run_id=$1 AND h.time <= $2 ORDER BY h.tail_no,h.time DESC,h.version DESC""",run_id,as_of)
            states=[_state(row) for row in rows]
        else:
            states=[_state(row) for row in await self.pool.fetch("SELECT * FROM aircraft.aircraft WHERE run_id=$1 ORDER BY tail_no",run_id)]
        def selected(s:AircraftState)->bool:
            return (not query.base_id or s.base_id==query.base_id) and (not query.type or s.type==query.type) and (not query.status or s.status==query.status) and set(query.capabilities).issubset(s.capabilities)
        states=[state for state in states if selected(state)]
        return {"run_id":run_id,"data_as_of":as_of,"items":states[:query.limit],"total":len(states),"counts_by_status":{x.value:sum(s.status is x for s in states) for x in AircraftStatus},"truncated":len(states)>query.limit,"projection":"sql"}
    async def get_state(self,tail_no:str)->AircraftState|None:
        row=await self.pool.fetchrow("SELECT * FROM aircraft.aircraft WHERE tail_no=$1",tail_no); return _state(row) if row else None
    async def get_counts(self,run_id:str,query:AvailabilityQuery)->dict[str,Any]:
        result=await self.get_aircraft_availability(query.model_copy(update={"limit":200}),run_id)
        return {"total":result["total"],"counts_by_status":result["counts_by_status"],"data_as_of":result["data_as_of"]}
    async def get_history(self,tail_no:str,since:datetime,until:datetime,limit:int=200)->list[AircraftState]:
        rows=await self.pool.fetch("""SELECT tail_no,run_id,a.type,a.sqn_id,h.base_id,h.status,h.status_reason,h.fuel_kg,h.flight_hours_total,h.hours_since_inspection,h.hours_to_next_inspection,h.maint_return_eta,h.capabilities,a.turnaround_min,h.status_since,h.ready_at,h.time AS updated_at,h.version,h.event_id AS last_event_id FROM aircraft.aircraft_status_history h JOIN aircraft.aircraft a USING(tail_no) WHERE h.tail_no=$1 AND h.time >= $2 AND h.time <= $3 ORDER BY h.time,h.version LIMIT $4""",tail_no,since,until,limit)
        return [_state(row) for row in rows]
    async def get_dispatch_candidates(self,run_id:str,base_id:str,available_at:datetime,min_fuel_kg:float,inspection_margin:float)->list[AircraftState]:
        items=(await self.get_aircraft_availability(AvailabilityQuery(base_id=base_id,status=AircraftStatus.FMC,limit=200),run_id))["items"]
        return [s for s in items if s.ready_at<=available_at and s.fuel_kg>=min_fuel_kg and s.hours_to_next_inspection>=inspection_margin]
    async def get_assignments(self,run_id:str,base_id:str)->list[dict[str,str]]:
        rows=await self.pool.fetch("SELECT tail_no,sqn_id,base_id FROM aircraft.aircraft WHERE run_id=$1 AND base_id=$2 ORDER BY tail_no",run_id,base_id); return [dict(row) for row in rows]
