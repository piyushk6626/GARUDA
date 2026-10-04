"""Only authoritative crew write path: row lock, state, envelope and outbox commit together."""
from __future__ import annotations
import hashlib,json
from datetime import datetime
from typing import Any
from ...schemas.crew import CrewCommand,CrewEvent,CrewState,DutyInterval,DutyKind
from ..fatigue.crew import fatigue_score

class ExpectedVersionConflict(RuntimeError): pass
class InvalidCrewTransition(ValueError): pass
def canonical(value:object)->str:return json.dumps(value,sort_keys=True,separators=(",",":"),default=str)
def _event_hash(event:CrewEvent)->str:return hashlib.sha256(canonical(event.model_dump(mode="json")).encode()).hexdigest()
def _state(row:Any)->CrewState:
    data=dict(row);data.pop("recorded_at",None)
    for key in ("qualifications","currency"):
        if isinstance(data.get(key),str):data[key]=json.loads(data[key])
    return CrewState.model_validate(data)

class CrewCommandService:
    def __init__(self,pool:Any)->None:self.pool=pool
    async def seed_all(self, *, run:dict[str,Any], states:tuple[CrewState,...], intervals:tuple[DutyInterval,...])->int:
        async with self.pool.acquire() as c, c.transaction():
            await c.execute("SELECT pg_advisory_xact_lock(hashtext($1))",run["run_id"])
            present=await c.fetchrow("SELECT configuration_hash FROM crew.scenario_run WHERE run_id=$1",run["run_id"])
            if present:
                if present["configuration_hash"]!=run["configuration_hash"]: raise ValueError("run_id exists with another configuration")
                return 0
            await c.execute("""INSERT INTO crew.scenario_run(run_id,seed,start_at,logical_clock,configuration_hash,catalog_hash,reference_hash,aircraft_run_id,generator_version,model_version,lifecycle_status)
              VALUES($1,$2,$3,$3,$4,$5,$6,$7,$8,$9,'seeded')""",run["run_id"],run["seed"],run["start_at"],run["configuration_hash"],run["catalog_hash"],run["reference_hash"],run["aircraft_run_id"],run["generator_version"],run["model_version"])
            grouped:dict[str,list[DutyInterval]]={}
            for interval in intervals: grouped.setdefault(interval.crew_id,[]).append(interval)
            for state in states: await self._seed(c,state,tuple(grouped.get(state.crew_id,())))
            await c.execute("UPDATE crew.scenario_run SET lifecycle_status='complete' WHERE run_id=$1",run["run_id"])
        return len(states)
    async def _seed(self,c:Any,state:CrewState,intervals:tuple[DutyInterval,...])->CrewEvent:
        event=CrewEvent.from_state(state,kind="seed",intervals=intervals)
        await c.execute("""INSERT INTO crew.crew(crew_id,run_id,callsign,role,sqn_id,base_id,primary_type,qualifications,currency,flight_hours_total,flight_hours_last_30d,duty_hours_24h,rest_hours_since_last_duty,fatigue_score,medical_status,leave_until,current_assignment,is_flight_lead,logical_time,updated_at,version,last_event_id)
          VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22)""",state.crew_id,state.run_id,state.callsign,state.role.value,state.sqn_id,state.base_id,state.primary_type,canonical(state.qualifications),state.currency.model_dump_json(),state.flight_hours_total,state.flight_hours_last_30d,state.duty_hours_24h,state.rest_hours_since_last_duty,state.fatigue_score,state.medical_status.value,state.leave_until,state.current_assignment,state.is_flight_lead,state.logical_time,state.updated_at,state.version,state.last_event_id)
        await self._qualifications(c,state)
        for interval in intervals: await self._interval(c,interval)
        await self._event_outbox(c,event,"seed",canonical({"state":state.model_dump(mode="json"),"intervals":[x.model_dump(mode="json") for x in intervals]}))
        return event
    async def apply(self,command:CrewCommand)->CrewEvent:
        async with self.pool.acquire() as c, c.transaction():
            row=await c.fetchrow("SELECT * FROM crew.crew WHERE crew_id=$1 AND run_id=$2 FOR UPDATE",command.crew_id,command.run_id)
            if not row: raise KeyError(command.crew_id)
            old=_state(row); command_hash=canonical(command.model_dump(mode="json")); duplicate=await c.fetchrow("SELECT envelope,command_hash FROM crew.crew_outbox WHERE run_id=$1 AND crew_id=$2 AND idempotency_key=$3",command.run_id,command.crew_id,command.idempotency_key)
            if duplicate:
                if duplicate["command_hash"]!=command_hash: raise InvalidCrewTransition("idempotency key reused with conflicting content")
                return CrewEvent.model_validate_json(duplicate["envelope"]) if isinstance(duplicate["envelope"],str) else CrewEvent.model_validate(duplicate["envelope"])
            if command.expected_version is not None and command.expected_version!=old.version: raise ExpectedVersionConflict(f"expected {command.expected_version}, current {old.version}")
            if command.event_time<old.logical_time: raise InvalidCrewTransition("logical event time cannot move backwards")
            changes=old.model_dump(); changes.update(logical_time=command.event_time,updated_at=command.event_time,version=old.version+1)
            if command.base_id is not None: changes["base_id"]=command.base_id
            if command.medical_status is not None: changes["medical_status"]=command.medical_status
            if command.leave_until is not None or command.kind=="leave": changes["leave_until"]=command.leave_until
            if command.qualifications is not None: changes["qualifications"]=command.qualifications
            if command.currency is not None: changes["currency"]=command.currency
            if command.kind=="sortie_start":
                if old.current_assignment: raise InvalidCrewTransition("crew is already assigned")
                changes["current_assignment"]=command.current_assignment or (command.interval.mission_id if command.interval else None)
            if command.kind=="sortie_end": changes["current_assignment"]=None
            interval=command.interval
            if interval:
                if interval.crew_id!=old.crew_id or interval.run_id!=old.run_id: raise InvalidCrewTransition("interval identity does not match command")
                await self._interval(c,interval)
                if interval.kind is DutyKind.FLIGHT and interval.end_at:
                    hours=(interval.end_at-interval.start_at).total_seconds()/3600; changes["flight_hours_total"]=old.flight_hours_total+hours; changes["flight_hours_last_30d"]=old.flight_hours_last_30d+hours
            intervals=await c.fetch("SELECT interval_id,crew_id,run_id,start_at,end_at,kind,mission_id,qualification,is_night AS night,is_aar AS aar FROM crew.duty_interval WHERE crew_id=$1",old.crew_id)
            parsed=tuple(DutyInterval.model_validate(dict(x)) for x in intervals)
            changes["fatigue_score"]=fatigue_score(parsed,command.event_time,prior=old.fatigue_score)
            event0=CrewEvent.from_state(CrewState.model_validate({**changes,"last_event_id":old.last_event_id}),kind=command.kind,intervals=(interval,) if interval else ())
            changes["last_event_id"]=event0.event_id; state=CrewState.model_validate(changes); event=CrewEvent.from_state(state,kind=command.kind,intervals=(interval,) if interval else ())
            await c.execute("""UPDATE crew.crew SET base_id=$1,qualifications=$2::jsonb,currency=$3::jsonb,flight_hours_total=$4,flight_hours_last_30d=$5,fatigue_score=$6,medical_status=$7,leave_until=$8,current_assignment=$9,logical_time=$10,updated_at=$10,recorded_at=clock_timestamp(),version=$11,last_event_id=$12 WHERE crew_id=$13""",state.base_id,canonical(state.qualifications),state.currency.model_dump_json(),state.flight_hours_total,state.flight_hours_last_30d,state.fatigue_score,state.medical_status.value,state.leave_until,state.current_assignment,state.logical_time,state.version,state.last_event_id,state.crew_id)
            if command.qualifications is not None: await c.execute("DELETE FROM crew.crew_qualification WHERE crew_id=$1",state.crew_id); await self._qualifications(c,state)
            await self._event_outbox(c,event,command.idempotency_key,command_hash); return event
    async def _interval(self,c:Any,item:DutyInterval)->None:
        await c.execute("INSERT INTO crew.duty_interval(interval_id,run_id,crew_id,start_at,end_at,kind,mission_id,qualification,is_night,is_aar) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",item.interval_id,item.run_id,item.crew_id,item.start_at,item.end_at,item.kind.value,item.mission_id,item.qualification,item.night,item.aar)
    async def _qualifications(self,c:Any,state:CrewState)->None:
        for kind,values in state.qualifications.items():
            for value in values: await c.execute("INSERT INTO crew.crew_qualification(crew_id,aircraft_type,qualification) VALUES($1,$2,$3)",state.crew_id,kind,value)
    async def _event_outbox(self,c:Any,event:CrewEvent,key:str,command_hash:str)->None:
        digest=_event_hash(event); state=event.state(); envelope=event.model_dump_json()
        await c.execute("INSERT INTO crew.crew_event_identity(event_id,run_id,crew_id,version,payload_hash,logical_time,envelope) VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)",event.event_id,state.run_id,state.crew_id,state.version,digest,event.ts,envelope)
        await c.execute("INSERT INTO crew.crew_outbox(event_id,run_id,crew_id,version,idempotency_key,command_hash,event_type,logical_time,envelope,payload_hash) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10)",event.event_id,state.run_id,state.crew_id,state.version,key,command_hash,event.type,event.ts,envelope,digest)
