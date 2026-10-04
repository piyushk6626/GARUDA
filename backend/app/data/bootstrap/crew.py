"""Reference bootstrap shared by seed and test paths; never writes aircraft state."""
from __future__ import annotations
import json
from typing import Any
from ...aircraft_config import load_scenario as load_aircraft
from ...data.generators.aircraft import generate_fleet
from ...crew_config import stable_hash

async def ensure_aircraft_references(connection:Any, *, aircraft_scenario_path:str, seed:int, run_id:str)->str:
    scenario=load_aircraft(aircraft_scenario_path,seed=seed); fleet=generate_fleet(scenario,run_id)
    digest=stable_hash(scenario.model_dump(mode="json"))
    existing=await connection.fetchrow("SELECT configuration_hash FROM aircraft.scenario_run WHERE run_id=$1 FOR UPDATE",run_id)
    if existing and existing["configuration_hash"]!=digest: raise ValueError("aircraft reference run conflicts with scenario")
    if not existing:
        await connection.execute("INSERT INTO aircraft.scenario_run(run_id,seed,start_at,configuration_hash,generator_version,logical_clock,lifecycle_status) VALUES($1,$2,$3,$4,$5,$3,'complete')",run_id,seed,scenario.start_at,digest,scenario.generator_version)
        for base in scenario.bases: await connection.execute("INSERT INTO aircraft.base(run_id,base_id,name,icao,role) VALUES($1,$2,$3,$4,$5)",run_id,base.base_id,base.name,base.icao,base.role)
        for kind in fleet.types: await connection.execute("INSERT INTO aircraft.aircraft_type(type,fuel_capacity_kg,inspection_cycle_hours,turnaround_min,range_km,cruise_kts,ceiling_ft,capabilities) VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(type) DO NOTHING",kind.type,kind.fuel_capacity_kg,kind.inspection_cycle_hours,kind.turnaround_min,kind.range_km,kind.cruise_kts,kind.ceiling_ft,list(kind.capabilities))
        for squadron in fleet.squadrons: await connection.execute("INSERT INTO aircraft.squadron(run_id,sqn_id,home_base_id,type,name,role) VALUES($1,$2,$3,$4,$5,$6)",run_id,squadron['sqn_id'],squadron['home_base_id'],squadron['type'],squadron['name'],squadron['role'])
    return digest
