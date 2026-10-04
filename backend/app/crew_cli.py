"""Operational crew commands. Every subcommand touches its real adapter."""
from __future__ import annotations
import argparse,asyncio,os
from pathlib import Path
import asyncpg
from aiokafka import AIOKafkaConsumer,AIOKafkaProducer
from neo4j import AsyncGraphDatabase
from pydantic_settings import BaseSettings,SettingsConfigDict
from redis.asyncio import from_url as redis_from_url
from .crew_config import load_catalog,load_scenario,scenario_hash,stable_hash
from .data.bootstrap.crew import ensure_aircraft_references
from .data.generators.crew import generate_crew
from .data.services.crew import CrewCommandService
from .data.outbox.crew import CrewOutboxRelay,STATUS_TOPIC,EVENTS_TOPIC
from .data.consumers.crew_history import CrewHistoryWriter,CrewHistoryConsumer
from .data.consumers.crew_live import CrewLiveProjection,CrewLiveConsumer
from .data.consumers.crew_graph import CrewGraphProjection,CrewGraphConsumer
from .data.reconciliation.crew import CrewReconciler
from .data.repositories.crew import CrewRepository
from .data.simulation.crew import CrewSimulator
class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file="infra/crew/.env",env_prefix="CREW_",extra="ignore")
    postgres_dsn:str=os.getenv("GARUDA_POSTGRES_DSN","postgresql://garuda:garuda-crew-local-only@localhost:45432/garuda_crew")
    kafka_bootstrap_servers:str="localhost:49092";redis_url:str="redis://:garuda-crew-redis-local-only@localhost:46379/0";neo4j_uri:str="bolt://localhost:47687";neo4j_password:str="garuda-crew-neo4j-local-only"
async def generate(args):
    scenario=load_scenario(args.config,seed=args.seed);seed=generate_crew(scenario,args.run_id);pool=await asyncpg.create_pool(Settings().postgres_dsn)
    try:
        async with pool.acquire() as c, c.transaction():reference=await ensure_aircraft_references(c,aircraft_scenario_path=scenario.aircraft_scenario,seed=scenario.seed,run_id=args.run_id)
        catalog=load_catalog(scenario.catalog);run={"run_id":args.run_id,"seed":scenario.seed,"start_at":scenario.start_at,"configuration_hash":scenario_hash(scenario),"catalog_hash":stable_hash(catalog.model_dump(mode="json")),"reference_hash":reference,"aircraft_run_id":args.run_id,"generator_version":scenario.generator_version,"model_version":"crew-fatigue-v1"}
        count=await CrewCommandService(pool).seed_all(run=run,states=seed.states,intervals=seed.intervals);print(f"seeded {count} crew; checksum {seed.checksum()}")
    finally:await pool.close()
async def simulate(args):
    pool=await asyncpg.create_pool(Settings().postgres_dsn)
    try:print(f"simulated {await CrewSimulator(pool).run(args.run_id,args.horizon_hours,speed=args.speed)} hourly state updates")
    finally:await pool.close()
async def worker(args):
    settings=Settings();pool=await asyncpg.create_pool(settings.postgres_dsn);producer=AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers,enable_idempotence=True,acks="all");await producer.start()
    try:
        if args.role=="outbox":
            relay=CrewOutboxRelay(pool,producer)
            while True:
                await relay.relay_once()
                if not args.forever:break
                await asyncio.sleep(0.1)
        else:
            topic=EVENTS_TOPIC if args.role=="history" else STATUS_TOPIC;consumer=AIOKafkaConsumer(topic,bootstrap_servers=settings.kafka_bootstrap_servers,group_id=f"garuda-crew-{args.role}",enable_auto_commit=False,auto_offset_reset="earliest");await consumer.start()
            try:
                if args.role=="history":handler=CrewHistoryConsumer(consumer,CrewHistoryWriter(pool))
                elif args.role=="live":handler=CrewLiveConsumer(consumer,CrewLiveProjection(redis_from_url(settings.redis_url,decode_responses=True)))
                else:
                    graph=CrewGraphProjection(AsyncGraphDatabase.driver(settings.neo4j_uri,auth=("neo4j",settings.neo4j_password)));await graph.bootstrap();handler=CrewGraphConsumer(consumer,graph)
                while True:
                    await handler.process_one()
                    if not args.forever:break
            finally:await consumer.stop()
    finally:await producer.stop();await pool.close()
async def reconcile(args):
    s=Settings();pool=await asyncpg.create_pool(s.postgres_dsn);redis=redis_from_url(s.redis_url,decode_responses=True);driver=AsyncGraphDatabase.driver(s.neo4j_uri,auth=("neo4j",s.neo4j_password))
    try:print(f"repaired {await CrewReconciler(pool,live=CrewLiveProjection(redis),graph=CrewGraphProjection(driver)).rebuild(args.run_id)} projections")
    finally:await redis.aclose();await driver.close();await pool.close()
async def query(args):
    pool=await asyncpg.create_pool(Settings().postgres_dsn)
    try:print([x.model_dump(mode="json") for x in (await CrewRepository(pool).list_crew(__import__('backend.app.schemas.crew',fromlist=['CrewQuery']).CrewQuery(run_id=args.run_id,limit=args.limit)))["items"]])
    finally:await pool.close()
async def check(args):
    pool=await asyncpg.create_pool(Settings().postgres_dsn)
    try:
        pending=await pool.fetchval("SELECT count(*) FROM crew.crew_outbox WHERE status_published_at IS NULL OR events_published_at IS NULL OR history_projected_at IS NULL");print(f"pending_outbox={pending}")
        if pending:raise RuntimeError("crew outbox has unresolved records")
    finally:await pool.close()
def main():
    p=argparse.ArgumentParser(prog="garuda-crew");sub=p.add_subparsers(required=True)
    x=sub.add_parser("generate");x.add_argument("--config",default="config/scenarios/crew_demo.yaml");x.add_argument("--seed",type=int);x.add_argument("--run-id",default="demo-1");x.set_defaults(handler=generate)
    x=sub.add_parser("simulate");x.add_argument("--run-id",default="demo-1");x.add_argument("--horizon-hours",type=int,default=72);x.add_argument("--speed",type=float,default=60);x.set_defaults(handler=simulate)
    x=sub.add_parser("worker");x.add_argument("role",choices=("outbox","history","live","graph"));x.add_argument("--forever",action="store_true");x.set_defaults(handler=worker)
    for name,fn in (("reconcile",reconcile),("query",query),("check",check)):
        x=sub.add_parser(name);x.add_argument("--run-id",default="demo-1");x.add_argument("--limit",type=int,default=50);x.set_defaults(handler=fn)
    args=p.parse_args();result=args.handler(args);asyncio.run(result) if asyncio.iscoroutine(result) else None
if __name__=="__main__":main()
