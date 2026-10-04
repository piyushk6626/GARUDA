"""Bounded SQL query adapter; eligibility is evaluated at the requested time."""
from __future__ import annotations
from datetime import datetime
import json
from typing import Any
from ...schemas.crew import CrewAvailabilityQuery,CrewQuery,CrewState,MedicalStatus
from ..fatigue.crew import fatigue_band

def _state(row:Any)->CrewState:
    data=dict(row);data.pop("recorded_at",None)
    for key in ("qualifications","currency"):
        if isinstance(data.get(key),str):data[key]=json.loads(data[key])
    return CrewState.model_validate(data)
class CrewRepository:
    def __init__(self,pool:Any,*,cache:Any|None=None)->None:self.pool,self.cache=pool,cache
    async def get_state(self,run_id:str,crew_id:str,*,at:datetime|None=None)->CrewState|None:
        if at is None: row=await self.pool.fetchrow("SELECT * FROM crew.crew WHERE run_id=$1 AND crew_id=$2",run_id,crew_id)
        else: row=await self.pool.fetchrow("SELECT state FROM crew.crew_duty_log WHERE run_id=$1 AND crew_id=$2 AND time <= $3 ORDER BY time DESC,version DESC LIMIT 1",run_id,crew_id,at)
        if not row:return None
        return CrewState.model_validate_json(row["state"]) if at and isinstance(row["state"],str) else (CrewState.model_validate(row["state"]) if at else _state(row))
    async def list_crew(self,query:CrewQuery)->dict[str,Any]:
        rows=await self.pool.fetch("SELECT * FROM crew.crew WHERE run_id=$1 AND ($2::text IS NULL OR base_id=$2) AND ($3::text IS NULL OR role=$3) AND ($4::text IS NULL OR medical_status=$4) AND ($5::text IS NULL OR crew_id>$5) ORDER BY crew_id LIMIT $6",query.run_id,query.base_id,query.role.value if query.role else None,query.medical_status.value if query.medical_status else None,query.cursor,query.limit+1)
        states=[_state(row) for row in rows];return {"run_id":query.run_id,"items":states[:query.limit],"next_cursor":states[query.limit].crew_id if len(states)>query.limit else None}
    @staticmethod
    def blockers(state:CrewState,q:CrewAvailabilityQuery)->list[str]:
        result=[]
        if state.role is not q.required_role:result.append("role")
        if q.aircraft_type not in state.qualifications:result.append("qualification")
        elif q.qualification and q.qualification not in state.qualifications[q.aircraft_type]:result.append("qualification")
        current=state.currency.qualification.get(q.aircraft_type)
        if not current or (q.at-current).days>90:result.append("currency")
        if q.require_night and (not state.currency.night or (q.at-state.currency.night).days>90):result.append("night_currency")
        if q.require_aar and (not state.currency.aar or (q.at-state.currency.aar).days>180):result.append("aar_currency")
        if state.medical_status is not MedicalStatus.FIT:result.append("medical")
        if state.leave_until and state.leave_until>q.at:result.append("leave")
        if state.current_assignment:result.append("assignment")
        if state.rest_hours_since_last_duty<12 and state.duty_hours_24h==0:result.append("rest")
        projected=state.duty_hours_24h+(q.slot_end-q.at).total_seconds()/3600
        if projected>14:result.append("duty")
        if state.fatigue_score>.7:result.append("fatigue")
        return result
    async def get_available_crew(self,q:CrewAvailabilityQuery)->dict[str,Any]:
        listed=await self.list_crew(q); evaluated=[]
        for state in listed["items"]:
            why=self.blockers(state,q);evaluated.append({"state":state,"eligible":not why,"blocking_reasons":why,"fatigue_band":fatigue_band(state.fatigue_score)})
        return {"run_id":q.run_id,"evaluation_time":q.at,"items":evaluated,"eligible":[x["state"] for x in evaluated if x["eligible"]],"next_cursor":listed["next_cursor"]}
    async def get_duty_history(self,run_id:str,crew_id:str,limit:int=200)->list[dict[str,Any]]:
        if not 1<=limit<=500:raise ValueError("limit out of range")
        return [dict(row) for row in await self.pool.fetch("SELECT * FROM crew.crew_duty_log WHERE run_id=$1 AND crew_id=$2 ORDER BY time DESC,version DESC LIMIT $3",run_id,crew_id,limit)]
    async def get_qualification_summary(self,run_id:str)->list[dict[str,Any]]:
        return [dict(r) for r in await self.pool.fetch("SELECT q.aircraft_type,q.qualification,count(*) AS crew_count FROM crew.crew_qualification q JOIN crew.crew c USING(crew_id) WHERE c.run_id=$1 GROUP BY q.aircraft_type,q.qualification ORDER BY q.aircraft_type,q.qualification",run_id)]
