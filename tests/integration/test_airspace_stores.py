"""One consolidated airspace store suite; integration cases use the real compose services."""
from __future__ import annotations
import asyncio, hashlib, json, os, uuid
from datetime import datetime, timezone
from pathlib import Path
import asyncpg, boto3, pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from neo4j import AsyncGraphDatabase
from redis.asyncio import from_url as redis_from_url
from backend.app.airspace_config import load_scenario
from backend.app.data.airspace_archive import AirspaceArchive
from backend.app.data.consumers.airspace_live import AirspaceLiveProjection
from backend.app.data.generators.airspace import generate_airspace
from backend.app.data.ingestors.openaip import fabricated_zones
from backend.app.data.outbox.airspace import ACTIVATION_TOPIC
from backend.app.schemas.airspace import AirspaceEvent, AltitudeLimit

UTC=timezone.utc; ROOT=Path(__file__).parents[2]; enabled=os.getenv("GARUDA_AIRSPACE_INTEGRATION")=="1"
def _integration():
    if not enabled: pytest.skip("set GARUDA_AIRSPACE_INTEGRATION=1 and use infra/airspace compose stack")
def _scenario(): return load_scenario(ROOT/"config/scenarios/airspace_demo.yaml",seed=42)

@pytest.mark.parametrize("seed",[42,43])
def test_airspace_validation_and_generation(seed:int):
    generated=generate_airspace(_scenario().model_copy(update={"seed":seed}),"fixture")
    assert len(generated.zones)==20 and generated.zones==tuple(sorted(generated.zones,key=lambda z:z.zone_id))
    assert generated.zones[0].geometry["type"] in {"Polygon","MultiPolygon"}
    assert generated.zones != generate_airspace(_scenario().model_copy(update={"seed":seed+1}),"fixture").zones
    assert AltitudeLimit(value=1000,unit="m",datum="AMSL").amsl_ft()[0] == 3281
    assert AltitudeLimit(unit="agl",datum="AGL").amsl_ft()[1]
    if seed==42:
        snapshot=json.loads((ROOT/"tests/fixtures/airspace/seed42_snapshot.json").read_text())
        assert snapshot==[z.model_dump(mode="json") for z in generated.zones]

async def _pg(): return await asyncpg.connect(host=os.getenv("AIRSPACE_PG_HOST","localhost"),port=int(os.getenv("AIRSPACE_PG_PORT","35432")),user=os.getenv("AIRSPACE_PG_USER","garuda"),password=os.getenv("AIRSPACE_PG_PASSWORD","garuda-airspace-local-only"),database=os.getenv("AIRSPACE_PG_DATABASE","garuda_airspace"))
@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_airspace_geometry_and_outbox():
    _integration(); con=await _pg()
    try:
        assert await con.fetchval("SELECT EXISTS(SELECT 1 FROM pg_indexes WHERE schemaname='airspace' AND indexname='airspace_zone_geometry_gix')")
        assert await con.fetchval("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='airspace' AND table_name='airspace_outbox')")
    finally: await con.close()
@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_airspace_active_set():
    _integration(); client=redis_from_url(os.environ["AIRSPACE_REDIS_URL"],decode_responses=True); run=f"it-{uuid.uuid4().hex[:12]}"
    try:
        z=generate_airspace(_scenario(),run).zones[0]; event=AirspaceEvent.from_zone(z,at=_scenario().start_at,active=True)
        projection=AirspaceLiveProjection(client); assert await projection.apply(event); assert z.zone_id in await client.smembers(f"airspace:{run}:active")
        assert not await projection.apply(event)
    finally: await client.delete(f"airspace:{run}:active",f"airspace:{run}:zone:{z.zone_id}"); await client.aclose()
@pytest.mark.integration
@pytest.mark.asyncio
async def test_neo4j_airspace_mission_links():
    _integration(); driver=AsyncGraphDatabase.driver(os.environ["AIRSPACE_NEO4J_URI"],auth=("neo4j",os.environ["AIRSPACE_NEO4J_PASSWORD"]));
    try:
        async with driver.session() as s: assert await (await s.run("SHOW CONSTRAINTS YIELD name WHERE name='airspace_zone_unique' RETURN count(*) AS n")).single()
    finally: await driver.close()
@pytest.mark.integration
def test_minio_openaip_archive_and_replay():
    _integration(); c=boto3.client("s3",endpoint_url=os.environ["AIRSPACE_MINIO_ENDPOINT"],aws_access_key_id=os.environ["AIRSPACE_MINIO_ROOT_USER"],aws_secret_access_key=os.environ["AIRSPACE_MINIO_ROOT_PASSWORD"],region_name="us-east-1")
    raw=(ROOT/"tests/fixtures/airspace/fabricated_openaip.json").read_bytes(); archive=AirspaceArchive(c); manifest=archive.archive_pages([raw],source="fabricated",fabricated=True)
    assert archive.replay(manifest.input_sha256)==[raw] and manifest.input_sha256==hashlib.sha256(raw).hexdigest()
@pytest.mark.integration
@pytest.mark.asyncio
async def test_redpanda_airspace_activation_roundtrip():
    _integration(); brokers=os.environ["AIRSPACE_KAFKA_BOOTSTRAP_SERVERS"]; group=f"airspace-test-{uuid.uuid4()}"; consumer=AIOKafkaConsumer(ACTIVATION_TOPIC,bootstrap_servers=brokers,group_id=group,auto_offset_reset="latest"); producer=AIOKafkaProducer(bootstrap_servers=brokers,enable_idempotence=True,acks="all")
    await consumer.start(); await producer.start()
    try:
        await producer.send_and_wait(ACTIVATION_TOPIC,b'{"bad":false}',key=b"test"); message=await asyncio.wait_for(consumer.getone(),10); assert message.key==b"test"
    finally: await producer.stop(); await consumer.stop()
@pytest.mark.integration
@pytest.mark.asyncio
async def test_airspace_pipeline_and_recovery():
    _integration(); raw=(ROOT/"tests/fixtures/airspace/fabricated_openaip.json").read_bytes(); assert fabricated_zones(raw,"pipeline")
    # Compose has migrated services and a real topic; this is deliberately a contract smoke test for offline recovery inputs.
    assert len(generate_airspace(_scenario(),"pipeline").zones)==20
