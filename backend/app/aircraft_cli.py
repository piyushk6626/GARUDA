"""Aircraft availability operational CLI; ADS-B's CLI is intentionally untouched."""
from __future__ import annotations
import argparse, asyncio, os
import asyncpg
from pydantic_settings import BaseSettings, SettingsConfigDict
from .aircraft_config import load_scenario, scenario_hash
from .data.generators.aircraft import generate_fleet
from .data.services.aircraft import AircraftCommandService

class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file="infra/aircraft/.env",env_prefix="AIRCRAFT_",extra="ignore")
    postgres_dsn:str=os.getenv("GARUDA_POSTGRES_DSN","postgresql://garuda:garuda-aircraft-local-only@localhost:25432/garuda_aircraft")

async def generate(args:argparse.Namespace)->None:
    scenario=load_scenario(args.config,seed=args.seed); fleet=generate_fleet(scenario,args.run_id); pool=await asyncpg.create_pool(Settings().postgres_dsn)
    try:
        async with pool.acquire() as conn,conn.transaction():
            prior=await conn.fetchrow("SELECT configuration_hash FROM aircraft.scenario_run WHERE run_id=$1 FOR UPDATE",args.run_id); digest=scenario_hash(scenario)
            if prior:
                if prior["configuration_hash"] != digest: raise RuntimeError("run_id already exists with another configuration; use a new run_id")
                print(f"{args.run_id}: already seeded"); return
            await conn.execute("INSERT INTO aircraft.scenario_run(run_id,seed,start_at,configuration_hash,generator_version,logical_clock,lifecycle_status) VALUES($1,$2,$3,$4,$5,$3,'seeded')",args.run_id,scenario.seed,scenario.start_at,digest,scenario.generator_version)
            for base in scenario.bases: await conn.execute("INSERT INTO aircraft.base(run_id,base_id,name,icao,role) VALUES($1,$2,$3,$4,$5)",args.run_id,base.base_id,base.name,base.icao,base.role)
            for item in fleet.types: await conn.execute("INSERT INTO aircraft.aircraft_type(type,fuel_capacity_kg,inspection_cycle_hours,turnaround_min,range_km,cruise_kts,ceiling_ft,capabilities) VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(type) DO NOTHING",item.type,item.fuel_capacity_kg,item.inspection_cycle_hours,item.turnaround_min,item.range_km,item.cruise_kts,item.ceiling_ft,list(item.capabilities))
            for squadron in fleet.squadrons: await conn.execute("INSERT INTO aircraft.squadron(run_id,sqn_id,home_base_id,type,name,role) VALUES($1,$2,$3,$4,$5,$6)",args.run_id,squadron['sqn_id'],squadron['home_base_id'],squadron['type'],squadron['name'],squadron['role'])
        service=AircraftCommandService(pool)
        for state in fleet.states: await service.seed(state,idempotency_key="seed")
        await pool.execute("UPDATE aircraft.scenario_run SET lifecycle_status='complete' WHERE run_id=$1",args.run_id)
        print(f"seeded {len(fleet.states)} aircraft; checksum {fleet.checksum()}")
    finally: await pool.close()

def main()->None:
    parser=argparse.ArgumentParser(prog="garuda-aircraft"); subs=parser.add_subparsers(required=True)
    make=subs.add_parser("generate"); make.add_argument("--config",default="config/scenarios/aircraft_demo.yaml"); make.add_argument("--run-id",default="demo-1"); make.add_argument("--seed",type=int); make.set_defaults(handler=generate)
    worker=subs.add_parser("worker"); worker.add_argument("role",choices=("outbox","history","live","graph")); worker.set_defaults(handler=lambda _: print("worker role is configured through application adapters"))
    for name in ("simulate","reconcile","query"):
        item=subs.add_parser(name); item.set_defaults(handler=lambda _: print(f"{name}: use the application adapter from the deployment"))
    args=parser.parse_args(); result=args.handler(args)
    if asyncio.iscoroutine(result): asyncio.run(result)
