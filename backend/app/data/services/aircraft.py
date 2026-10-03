"""PostgreSQL command service: state and outbox are one transaction."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from ...schemas.aircraft import AircraftCommand, AircraftEvent, AircraftState, AircraftStatus


class ExpectedVersionConflict(RuntimeError): pass
class InvalidTransition(ValueError): pass


def _hash(event: AircraftEvent) -> str:
    return hashlib.sha256(event.model_dump_json(exclude={"event_id"}).encode()).hexdigest()


def _state(row: Any) -> AircraftState:
    data = dict(row)
    data.pop("recorded_at", None)
    data["capabilities"] = tuple(data["capabilities"])
    return AircraftState.model_validate(data)


class AircraftCommandService:
    def __init__(self, pool: Any) -> None: self.pool = pool

    async def seed(self, state: AircraftState, *, idempotency_key: str = "seed") -> AircraftEvent:
        event = AircraftEvent.from_state(state, kind="seed", prior_status=None)
        async with self.pool.acquire() as connection, connection.transaction():
            existing = await connection.fetchrow("SELECT envelope FROM aircraft.aircraft_outbox WHERE run_id=$1 AND tail_no=$2 AND idempotency_key=$3", state.run_id, state.tail_no, idempotency_key)
            if existing: return AircraftEvent.model_validate_json(existing["envelope"]) if isinstance(existing["envelope"], str) else AircraftEvent.model_validate(existing["envelope"])
            await connection.execute("""INSERT INTO aircraft.aircraft(tail_no,run_id,type,sqn_id,base_id,status,status_reason,fuel_kg,flight_hours_total,hours_since_inspection,hours_to_next_inspection,maint_return_eta,capabilities,turnaround_min,status_since,ready_at,updated_at,version,last_event_id)
              VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)""",
                state.tail_no,state.run_id,state.type,state.sqn_id,state.base_id,state.status.value,state.status_reason,state.fuel_kg,state.flight_hours_total,state.hours_since_inspection,state.hours_to_next_inspection,state.maint_return_eta,list(state.capabilities),state.turnaround_min,state.status_since,state.ready_at,state.updated_at,state.version,state.last_event_id)
            await self._outbox(connection,event,idempotency_key)
        return event

    async def apply(self, command: AircraftCommand) -> AircraftEvent:
        async with self.pool.acquire() as connection, connection.transaction():
            old_row = await connection.fetchrow("SELECT * FROM aircraft.aircraft WHERE tail_no=$1 AND run_id=$2 FOR UPDATE", command.tail_no, command.run_id)
            if not old_row: raise KeyError(command.tail_no)
            old = _state(old_row)
            duplicate = await connection.fetchrow("SELECT envelope FROM aircraft.aircraft_outbox WHERE run_id=$1 AND tail_no=$2 AND idempotency_key=$3", command.run_id,command.tail_no,command.idempotency_key)
            if duplicate: return AircraftEvent.model_validate_json(duplicate["envelope"]) if isinstance(duplicate["envelope"], str) else AircraftEvent.model_validate(duplicate["envelope"])
            if command.expected_version is not None and command.expected_version != old.version:
                raise ExpectedVersionConflict(f"expected {command.expected_version}, current {old.version}")
            if command.event_time < old.updated_at: raise InvalidTransition("logical event time cannot move backwards")
            data = old.model_dump()
            data.update(updated_at=command.event_time, version=old.version + 1)
            if command.base_id is not None: data["base_id"] = command.base_id
            if command.fuel_kg is not None: data["fuel_kg"] = command.fuel_kg
            if command.status is not None:
                self._transition(old.status, command.status, command.kind)
                data.update(status=command.status, status_reason=command.reason, status_since=command.event_time,
                            maint_return_eta=command.maint_return_eta)
            if command.kind == "inspection":
                data.update(hours_since_inspection=0.0, hours_to_next_inspection=float(command.metadata["inspection_cycle_hours"]))
            data["last_event_id"] = AircraftEvent.from_state(AircraftState.model_validate(data), kind=command.kind, prior_status=old.status, metadata=command.metadata).event_id
            state = AircraftState.model_validate(data)
            event = AircraftEvent.from_state(state, kind=command.kind, prior_status=old.status, metadata=command.metadata)
            await connection.execute("""UPDATE aircraft.aircraft SET base_id=$1,status=$2,status_reason=$3,fuel_kg=$4,flight_hours_total=$5,hours_since_inspection=$6,hours_to_next_inspection=$7,maint_return_eta=$8,status_since=$9,ready_at=$10,updated_at=$11,recorded_at=clock_timestamp(),version=$12,last_event_id=$13 WHERE tail_no=$14""",
                state.base_id,state.status.value,state.status_reason,state.fuel_kg,state.flight_hours_total,state.hours_since_inspection,state.hours_to_next_inspection,state.maint_return_eta,state.status_since,state.ready_at,state.updated_at,state.version,state.last_event_id,state.tail_no)
            await self._outbox(connection,event,command.idempotency_key)
            return event

    @staticmethod
    def _transition(old: AircraftStatus, new: AircraftStatus, kind: str) -> None:
        allowed = {AircraftStatus.FMC:{AircraftStatus.FMC,AircraftStatus.PMC,AircraftStatus.NMC,AircraftStatus.AOG,AircraftStatus.IN_MISSION}, AircraftStatus.PMC:{AircraftStatus.PMC,AircraftStatus.NMC,AircraftStatus.AOG,AircraftStatus.FMC,AircraftStatus.IN_MISSION}, AircraftStatus.NMC:{AircraftStatus.NMC,AircraftStatus.AOG,AircraftStatus.FMC,AircraftStatus.PMC}, AircraftStatus.AOG:{AircraftStatus.AOG,AircraftStatus.NMC,AircraftStatus.FMC,AircraftStatus.PMC}, AircraftStatus.IN_MISSION:{AircraftStatus.FMC,AircraftStatus.PMC,AircraftStatus.NMC,AircraftStatus.AOG}}
        if new not in allowed[old]: raise InvalidTransition(f"{old} -> {new} is not legal")
        if old in (AircraftStatus.NMC, AircraftStatus.AOG) and new is AircraftStatus.IN_MISSION: raise InvalidTransition("unserviceable aircraft cannot dispatch")

    async def _outbox(self, connection: Any, event: AircraftEvent, key: str) -> None:
        await connection.execute("""INSERT INTO aircraft.aircraft_outbox(event_id,run_id,tail_no,version,event_type,logical_time,envelope,payload_hash,idempotency_key)
          VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9)""", event.event_id,event.payload["run_id"],event.entity_id,event.version,event.type,event.ts,event.model_dump_json(),_hash(event),key)
