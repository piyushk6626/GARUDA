"""Executable offline-first airspace operational commands."""
from __future__ import annotations
import argparse, asyncio, hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import asyncpg
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from neo4j import AsyncGraphDatabase
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import from_url as redis_from_url
from .airspace_config import load_scenario, scenario_hash
from .data.generators.airspace import generate_airspace
from .data.services.airspace import AirspaceCommandService
from .data.repositories.airspace import AirspaceRepository
from .data.outbox.airspace import AirspaceOutboxRelay, ACTIVATION_TOPIC
from .data.consumers.airspace_live import AirspaceLiveConsumer, AirspaceLiveProjection
from .data.consumers.airspace_graph import AirspaceGraphConsumer, AirspaceGraphProjection
from .data.simulation.airspace import AirspaceScheduler
from .data.ingestors.openaip import fabricated_zones, live_contract

class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file="infra/airspace/.env",env_prefix="AIRSPACE_",extra="ignore")
    postgres_dsn:str=os.getenv("GARUDA_POSTGRES_DSN","postgresql://garuda:garuda-airspace-local-only@localhost:35432/garuda_airspace")
    kafka_bootstrap_servers:str="localhost:39092"; redis_url:str="redis://:garuda-airspace-redis-local-only@localhost:36379/0"
    neo4j_uri:str="bolt://localhost:37687"; neo4j_password:str="garuda-airspace-neo4j-local-only"

async def generate(args:argparse.Namespace)->None:
    scenario=load_scenario(args.config,seed=args.seed); seed=generate_airspace(scenario,args.run_id); pool=await asyncpg.create_pool(Settings().postgres_dsn)
    try:
        digest=scenario_hash(scenario)
        async with pool.acquire() as con,con.transaction():
            existing=await con.fetchrow("SELECT configuration_hash FROM airspace.scenario_run WHERE run_id=$1 FOR UPDATE",args.run_id)
            if existing and existing["configuration_hash"]!=digest: raise RuntimeError("run_id exists with another configuration")
            if not existing: await con.execute("INSERT INTO airspace.scenario_run(run_id,seed,start_at,logical_clock,configuration_hash,generator_version,input_checksums,lifecycle_status) VALUES($1,$2,$3,$3,$4,$5,$6,'seeded')",args.run_id,scenario.seed,scenario.start_at,digest,scenario.generator_version,json.dumps({"border":scenario.border_sha256},sort_keys=True))
        await AirspaceCommandService(pool).seed(seed.zones,at=scenario.start_at)
        await pool.execute("UPDATE airspace.scenario_run SET lifecycle_status='complete' WHERE run_id=$1",args.run_id)
        print(f"seeded {len(seed.zones)} zones; checksum {seed.checksum()}")
    finally: await pool.close()

async def import_fixture(args:argparse.Namespace)->None:
    if args.live: live_contract()
    raw=Path(args.fixture).read_bytes(); zones=fabricated_zones(raw,args.run_id); pool=await asyncpg.create_pool(Settings().postgres_dsn)
    try:
        await AirspaceCommandService(pool).seed(zones,at=datetime.fromisoformat(args.at.replace("Z","+00:00")))
        print(f"imported {len(zones)} fabricated zones sha256={hashlib.sha256(raw).hexdigest()}")
    finally: await pool.close()

async def worker(args:argparse.Namespace)->None:
    settings=Settings(); pool=await asyncpg.create_pool(settings.postgres_dsn); producer=AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers,enable_idempotence=True,acks="all")
    await producer.start()
    try:
        if args.role=="outbox":
            relay=AirspaceOutboxRelay(pool,producer)
            while True:
                try:
                    print(await relay.relay_once())
                except Exception:
                    if not args.forever: raise
                    await asyncio.sleep(5)
                    continue
                if not args.forever: break
                await asyncio.sleep(1)
        elif args.role=="scheduler":
            scheduler=AirspaceScheduler(pool,AirspaceCommandService(pool))
            while True:
                try:
                    print(await scheduler.tick(args.run_id,datetime.now(timezone.utc)))
                except Exception:
                    if not args.forever: raise
                    await asyncio.sleep(5)
                    continue
                if not args.forever: break
                await asyncio.sleep(60)
        else:
            consumer=AIOKafkaConsumer(ACTIVATION_TOPIC,bootstrap_servers=settings.kafka_bootstrap_servers,group_id=f"airspace-{args.role}",enable_auto_commit=False,auto_offset_reset="earliest"); await consumer.start()
            try:
                if args.role=="live":
                    redis=redis_from_url(settings.redis_url,decode_responses=True); handler=AirspaceLiveConsumer(consumer,AirspaceLiveProjection(redis),producer)
                else:
                    driver=AsyncGraphDatabase.driver(settings.neo4j_uri,auth=("neo4j",settings.neo4j_password)); handler=AirspaceGraphConsumer(consumer,AirspaceGraphProjection(driver),producer)
                while True:
                    try:
                        await asyncio.wait_for(handler.process_one(),timeout=args.timeout)
                    except TimeoutError:
                        if args.forever:
                            continue
                        raise
                    if not args.forever: break
            finally: await consumer.stop()
    finally: await producer.stop(); await pool.close()

async def query(args:argparse.Namespace)->None:
    pool=await asyncpg.create_pool(Settings().postgres_dsn)
    try: print((await AirspaceRepository(pool).list_zones(args.run_id,limit=args.limit))["items"])
    finally: await pool.close()
def main()->None:
    parser=argparse.ArgumentParser(prog="garuda-airspace"); subs=parser.add_subparsers(required=True)
    p=subs.add_parser("generate"); p.add_argument("--config",default="config/scenarios/airspace_demo.yaml"); p.add_argument("--run-id",default="demo-1"); p.add_argument("--seed",type=int); p.set_defaults(handler=generate)
    p=subs.add_parser("import-openaip"); p.add_argument("--fixture",default="tests/fixtures/airspace/fabricated_openaip.json"); p.add_argument("--run-id",default="demo-1"); p.add_argument("--at",default="2026-10-04T00:00:00Z"); p.add_argument("--live",action="store_true"); p.set_defaults(handler=import_fixture)
    p=subs.add_parser("worker"); p.add_argument("role",choices=("outbox","live","graph","scheduler")); p.add_argument("--timeout",type=float,default=10); p.add_argument("--run-id",default="demo-1"); p.add_argument("--forever",action="store_true"); p.set_defaults(handler=worker)
    p=subs.add_parser("query"); p.add_argument("--run-id",default="demo-1"); p.add_argument("--limit",type=int,default=50); p.set_defaults(handler=query)
    for name in ("replay","reconcile","check"):
        p=subs.add_parser(name); p.set_defaults(handler=lambda _: print(f"{name}: use the repository/archive adapter with an explicit run and clock"))
    args=parser.parse_args(); result=args.handler(args)
    if asyncio.iscoroutine(result): asyncio.run(result)
