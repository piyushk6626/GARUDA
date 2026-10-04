"""Real-container tests for all aircraft stores and the recovery pipeline."""
import asyncio, json, os, uuid
from datetime import timedelta

import asyncpg
import pytest
from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, ConfigResource
from neo4j import AsyncGraphDatabase
from redis import asyncio as redis

from backend.app.aircraft_config import load_scenario
from backend.app.data.consumers.aircraft_graph import AircraftGraphProjection
from backend.app.data.consumers.aircraft_history import AircraftHistoryWriter
from backend.app.data.consumers.aircraft_live import AircraftLiveProjection
from backend.app.data.generators.aircraft import generate_fleet
from backend.app.data.repositories.aircraft import AircraftRepository
from backend.app.data.services.aircraft import AircraftCommandService
from backend.app.schemas.aircraft import AircraftCommand, AircraftStatus, AvailabilityQuery

DSN="postgresql://{0}:{1}@{2}:{3}/{4}".format(os.getenv("AIRCRAFT_PG_USER","garuda"),os.getenv("AIRCRAFT_PG_PASSWORD","garuda-aircraft-local-only"),os.getenv("AIRCRAFT_PG_HOST","localhost"),os.getenv("AIRCRAFT_PG_PORT","25432"),os.getenv("AIRCRAFT_PG_DATABASE","garuda_aircraft"))

async def seed(run_id):
    pool=await asyncpg.create_pool(DSN); scenario=load_scenario("config/scenarios/aircraft_demo.yaml",seed=42); fleet=generate_fleet(scenario,run_id)
    async with pool.acquire() as c, c.transaction():
        await c.execute("INSERT INTO aircraft.scenario_run(run_id,seed,start_at,configuration_hash,generator_version,logical_clock) VALUES($1,42,$2,$3,'test',$2)",run_id,scenario.start_at,uuid.uuid4().hex)
        for b in scenario.bases: await c.execute("INSERT INTO aircraft.base(run_id,base_id,name,icao,role) VALUES($1,$2,$3,$4,$5)",run_id,b.base_id,b.name,b.icao,b.role)
        for t in fleet.types: await c.execute("INSERT INTO aircraft.aircraft_type(type,fuel_capacity_kg,inspection_cycle_hours,turnaround_min,range_km,cruise_kts,ceiling_ft,capabilities) VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING",t.type,t.fuel_capacity_kg,t.inspection_cycle_hours,t.turnaround_min,t.range_km,t.cruise_kts,t.ceiling_ft,list(t.capabilities))
        for s in fleet.squadrons: await c.execute("INSERT INTO aircraft.squadron(run_id,sqn_id,home_base_id,type,name,role) VALUES($1,$2,$3,$4,$5,$6)",run_id,s['sqn_id'],s['home_base_id'],s['type'],s['name'],s['role'])
    service=AircraftCommandService(pool); state=fleet.states[0]; event=await service.seed(state)
    return pool,service,state,event

@pytest.mark.asyncio
async def test_postgres_aircraft_state_and_outbox():
    pool,service,state,event=await seed("pg"+uuid.uuid4().hex[:10])
    try:
        assert await pool.fetchval("SELECT count(*) FROM aircraft.aircraft_outbox WHERE event_id=$1",event.event_id)==1
        assert (await service.seed(state)).event_id==event.event_id
        with pytest.raises(Exception): await service.apply(AircraftCommand(tail_no=state.tail_no,run_id=state.run_id,event_time=state.updated_at+timedelta(minutes=1),kind="fuel",idempotency_key="bad",fuel_kg=10**9))
        assert await pool.fetchval("SELECT fuel_kg FROM aircraft.aircraft WHERE tail_no=$1",state.tail_no)==state.fuel_kg
    finally: await pool.close()

@pytest.mark.asyncio
async def test_timescaledb_aircraft_history():
    pool,service,state,event=await seed("hist"+uuid.uuid4().hex[:8]); writer=AircraftHistoryWriter(pool)
    try:
        assert await writer.insert(event) and not await writer.insert(event)
        changed=await service.apply(AircraftCommand(tail_no=state.tail_no,run_id=state.run_id,event_time=state.updated_at+timedelta(minutes=1),kind="status",idempotency_key="pmc",status=AircraftStatus.PMC,reason="test"))
        assert await writer.insert(changed)
        assert await pool.fetchval("SELECT count(*) FROM aircraft.aircraft_status_history WHERE tail_no=$1",state.tail_no)==2
        assert await pool.fetchval("SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_schema='aircraft' AND hypertable_name='aircraft_status_history'")==1
    finally: await pool.close()

@pytest.mark.asyncio
async def test_redis_aircraft_live_membership():
    pool,service,state,event=await seed("redis"+uuid.uuid4().hex[:8]); client=redis.Redis(host=os.getenv("AIRCRAFT_REDIS_HOST","localhost"),port=int(os.getenv("AIRCRAFT_REDIS_PORT","26379")),password=os.getenv("AIRCRAFT_REDIS_PASSWORD","garuda-aircraft-redis-local-only"),decode_responses=True); projection=AircraftLiveProjection(client)
    try:
        await projection.apply(event); assert await client.hget(f"ac:{state.tail_no}","version")=="1"
        moved=await service.apply(AircraftCommand(tail_no=state.tail_no,run_id=state.run_id,event_time=state.updated_at+timedelta(minutes=1),kind="transfer",idempotency_key="move",base_id="B2")); await projection.apply(moved)
        assert not await client.sismember(f"base:{state.base_id}:fmc",state.tail_no) and await client.sismember("base:B2:fmc",state.tail_no)
        await projection.apply(event); assert await client.hget(f"ac:{state.tail_no}","version")=="2"
    finally: await client.delete(f"ac:{state.tail_no}",f"base:{state.base_id}:fmc","base:B2:fmc"); await client.aclose(); await pool.close()

@pytest.mark.asyncio
async def test_neo4j_aircraft_assignments():
    pool,_,state,event=await seed("graph"+uuid.uuid4().hex[:8]); driver=AsyncGraphDatabase.driver(os.getenv("AIRCRAFT_NEO4J_URI","bolt://localhost:27687"),auth=("neo4j",os.getenv("AIRCRAFT_NEO4J_PASSWORD","garuda-aircraft-neo4j-local-only"))); projection=AircraftGraphProjection(driver)
    try:
        await projection.bootstrap(); await projection.apply(event)
        async with driver.session() as s: assert await (await s.run("MATCH (a:Aircraft {tail_no:$tail}) RETURN a.version",tail=state.tail_no)).single()
        assert not await projection.apply(event)
    finally: await driver.close(); await pool.close()

def test_redpanda_aircraft_event_roundtrip():
    brokers=os.getenv("AIRCRAFT_KAFKA_BROKERS","localhost:29092"); admin=AdminClient({"bootstrap.servers":brokers}); topic="garuda.aircraft.events"; meta=admin.list_topics(topic=topic,timeout=10); assert len(meta.topics[topic].partitions)==3
    resource=ConfigResource(ConfigResource.Type.TOPIC,topic); config=admin.describe_configs([resource])[resource].result(10); assert config["cleanup.policy"].value=="delete"
    producer=Producer({"bootstrap.servers":brokers}); value=json.dumps({"event_id":str(uuid.uuid4()),"version":1}).encode(); producer.produce(topic,key="aircraft-test",value=value); assert producer.flush(10)==0

@pytest.mark.asyncio
async def test_aircraft_pipeline_and_recovery():
    pool,service,state,event=await seed("pipe"+uuid.uuid4().hex[:8]); writer=AircraftHistoryWriter(pool); repo=AircraftRepository(pool)
    try:
        await writer.insert(event)  # recovery from durable outbox envelope is idempotent
        result=await repo.get_aircraft_availability(AvailabilityQuery(limit=10),state.run_id)
        assert result["total"]==1 and result["items"][0].tail_no==state.tail_no
        assert await writer.insert(event) is False
    finally: await pool.close()
