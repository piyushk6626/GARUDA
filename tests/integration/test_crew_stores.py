"""One consolidated crew suite. Container tests never skip when explicitly enabled."""
from __future__ import annotations
import asyncio, hashlib, json, os, uuid
from datetime import timedelta
import pytest
from backend.app.crew_config import load_scenario
from backend.app.data.generators.crew import generate_crew
from backend.app.data.fatigue.crew import fatigue_band, fatigue_score
from backend.app.schemas.crew import CrewAvailabilityQuery, CrewRole, DutyInterval, DutyKind

INTEGRATION=os.getenv("GARUDA_CREW_INTEGRATION")=="1"
DSN="postgresql://{0}:{1}@{2}:{3}/{4}".format(os.getenv("CREW_PG_USER","garuda"),os.getenv("CREW_PG_PASSWORD","garuda-crew-local-only"),os.getenv("CREW_PG_HOST","localhost"),os.getenv("CREW_PG_PORT","45432"),os.getenv("CREW_PG_DATABASE","garuda_crew"))
def integration_only(fn): return pytest.mark.integration(pytest.mark.skipif(not INTEGRATION,reason="set GARUDA_CREW_INTEGRATION=1 for isolated store suite")(fn))

@pytest.mark.parametrize("seed",[42,43])
def test_crew_validation_and_generation(seed):
    scenario=load_scenario("config/scenarios/crew_demo.yaml",seed=seed);generated=generate_crew(scenario,"fixture")
    assert len(generated.states)==300
    assert {role.value:sum(x.role is role for x in generated.states) for role in CrewRole}=={"PILOT":248,"WSO":40,"LOADMASTER":8,"AEW_OP":4}
    assert all(x.primary_type in x.qualifications and x.flight_hours_last_30d<=x.flight_hours_total for x in generated.states)
    assert len({x.crew_id for x in generated.states})==300 and len({x.callsign for x in generated.states})==300
    if seed==42:
        fixture=json.loads(open("tests/fixtures/crew/seed42_snapshot.json",encoding="utf-8").read());manifest=json.loads(open("tests/fixtures/crew/seed42_manifest.json",encoding="utf-8").read())
        assert generated.checksum()==fixture["state_checksum"]==manifest["checksum"]
    else: assert generated.checksum()!="db0da637215f357e374361214be2eabb9aa76c752acba297dd6a4fe21315cb7a" and generated.states[0].crew_id=="fixture-CR-001"

@pytest.mark.parametrize("hours,band",[(.7,"AMBER"),(.70001,"RED")])
def test_crew_duty_fatigue_and_eligibility(hours,band):
    assert fatigue_band(hours)==band
    scenario=load_scenario("config/scenarios/crew_demo.yaml");state=generate_crew(scenario,"logic").states[0]
    q=CrewAvailabilityQuery(run_id="logic",aircraft_type=state.primary_type,required_role=state.role,at=scenario.start_at,slot_end=scenario.start_at+timedelta(hours=4))
    from backend.app.data.repositories.crew import CrewRepository
    reasons=CrewRepository.blockers(state,q)
    assert ("duty" in reasons) == (state.duty_hours_24h+4>14)
    sleep=DutyInterval(crew_id="x",run_id="x",start_at=scenario.start_at-timedelta(hours=8),end_at=scenario.start_at,kind=DutyKind.SLEEP)
    assert fatigue_score((sleep,),scenario.start_at,prior=.7)<.7

async def _seed(run_id):
    import asyncpg
    from backend.app.data.bootstrap.crew import ensure_aircraft_references
    from backend.app.data.services.crew import CrewCommandService
    from backend.app.crew_config import load_catalog,scenario_hash,stable_hash
    scenario=load_scenario("config/scenarios/crew_demo.yaml",seed=42);seed=generate_crew(scenario,run_id);pool=await asyncpg.create_pool(DSN)
    async with pool.acquire() as c, c.transaction(): ref=await ensure_aircraft_references(c,aircraft_scenario_path=scenario.aircraft_scenario,seed=42,run_id=run_id)
    await CrewCommandService(pool).seed_all(run={"run_id":run_id,"seed":42,"start_at":scenario.start_at,"configuration_hash":scenario_hash(scenario),"catalog_hash":stable_hash(load_catalog().model_dump(mode="json")),"reference_hash":ref,"aircraft_run_id":run_id,"generator_version":scenario.generator_version,"model_version":"crew-fatigue-v1"},states=seed.states,intervals=seed.intervals)
    return pool,seed

@integration_only
@pytest.mark.asyncio
async def test_postgres_crew_state_and_outbox():
    pool,seed=await _seed("pg"+uuid.uuid4().hex[:10])
    try:
        assert await pool.fetchval("SELECT count(*) FROM crew.crew WHERE run_id=$1",seed.states[0].run_id)==300
        assert await pool.fetchval("SELECT count(*) FROM crew.crew_outbox WHERE run_id=$1",seed.states[0].run_id)==300
        assert await pool.fetchval("SELECT count(*) FROM pg_indexes WHERE schemaname='crew' AND indexname='crew_state_qualification_gin'")==1
    finally: await pool.close()

@integration_only
@pytest.mark.asyncio
async def test_timescaledb_crew_duty_log():
    from backend.app.data.consumers.crew_history import CrewHistoryWriter
    pool,seed=await _seed("hist"+uuid.uuid4().hex[:8])
    try:
        event=(await pool.fetchrow("SELECT envelope FROM crew.crew_event_identity WHERE crew_id=$1",seed.states[0].crew_id))["envelope"]
        from backend.app.schemas.crew import CrewEvent
        parsed=CrewEvent.model_validate_json(event) if isinstance(event,str) else CrewEvent.model_validate(event);writer=CrewHistoryWriter(pool);assert await writer.insert(parsed);assert not await writer.insert(parsed)
        assert await pool.fetchval("SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_schema='crew' AND hypertable_name='crew_duty_log'")==1
    finally: await pool.close()

@integration_only
@pytest.mark.asyncio
async def test_redis_crew_live_hash():
    from redis.asyncio import from_url
    from backend.app.data.consumers.crew_live import CrewLiveProjection
    from backend.app.schemas.crew import CrewEvent
    pool,seed=await _seed("redis"+uuid.uuid4().hex[:8]);client=from_url(os.getenv("CREW_REDIS_URL","redis://:garuda-crew-redis-local-only@localhost:46379/0"),decode_responses=True)
    try:
        raw=(await pool.fetchrow("SELECT envelope FROM crew.crew_event_identity WHERE crew_id=$1",seed.states[0].crew_id))["envelope"];event=CrewEvent.model_validate_json(raw) if isinstance(raw,str) else CrewEvent.model_validate(raw);projection=CrewLiveProjection(client);assert await projection.apply(event);assert not await projection.apply(event);assert await client.hget(f"crew:{event.entity_id}","version")=="1"
    finally: await client.delete(f"crew:{seed.states[0].crew_id}");await client.aclose();await pool.close()

@integration_only
@pytest.mark.asyncio
async def test_neo4j_crew_qualifications():
    from neo4j import AsyncGraphDatabase
    from backend.app.data.consumers.crew_graph import CrewGraphProjection
    from backend.app.schemas.crew import CrewEvent
    pool,seed=await _seed("graph"+uuid.uuid4().hex[:8]);driver=AsyncGraphDatabase.driver(os.getenv("CREW_NEO4J_URI","bolt://localhost:47687"),auth=("neo4j",os.getenv("CREW_NEO4J_PASSWORD","garuda-crew-neo4j-local-only")))
    try:
        raw=(await pool.fetchrow("SELECT envelope FROM crew.crew_event_identity WHERE crew_id=$1",seed.states[0].crew_id))["envelope"];event=CrewEvent.model_validate_json(raw) if isinstance(raw,str) else CrewEvent.model_validate(raw);projection=CrewGraphProjection(driver);await projection.bootstrap();assert await projection.apply(event)
    finally:await driver.close();await pool.close()

@integration_only
def test_redpanda_crew_status_roundtrip():
    from confluent_kafka import Consumer,Producer
    from confluent_kafka.admin import AdminClient
    brokers=os.getenv("CREW_KAFKA_BROKERS","localhost:49092");topic="garuda.crew.status";admin=AdminClient({"bootstrap.servers":brokers});assert len(admin.list_topics(topic=topic,timeout=10).topics[topic].partitions)==3
    key="crew-test-"+uuid.uuid4().hex;producer=Producer({"bootstrap.servers":brokers});producer.produce(topic,key=key,value=json.dumps({"event_id":str(uuid.uuid4()),"version":1}).encode());assert producer.flush(10)==0
    consumer=Consumer({"bootstrap.servers":brokers,"group.id":"crew-roundtrip-"+uuid.uuid4().hex,"auto.offset.reset":"earliest"});consumer.subscribe([topic]);found=False
    for _ in range(20):
        msg=consumer.poll(.5)
        if msg and not msg.error() and msg.key().decode()==key:found=True;break
    consumer.close();assert found

@integration_only
@pytest.mark.asyncio
async def test_crew_pipeline_and_recovery():
    from backend.app.data.consumers.crew_history import CrewHistoryWriter
    pool,seed=await _seed("pipe"+uuid.uuid4().hex[:8])
    try:
        row=await pool.fetchrow("SELECT envelope FROM crew.crew_event_identity WHERE crew_id=$1",seed.states[0].crew_id)
        from backend.app.schemas.crew import CrewEvent
        event=CrewEvent.model_validate_json(row["envelope"]) if isinstance(row["envelope"],str) else CrewEvent.model_validate(row["envelope"]);assert await CrewHistoryWriter(pool).insert(event)
        assert await pool.fetchval("SELECT count(*) FROM crew.crew_duty_log WHERE crew_id=$1",seed.states[0].crew_id)==1
    finally:await pool.close()
