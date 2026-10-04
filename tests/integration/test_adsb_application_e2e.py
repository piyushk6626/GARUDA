"""Offline application pipeline acceptance test; requires the Compose stores."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import pytest
from aiokafka import AIOKafkaConsumer
from redis import asyncio as redis

from backend.app.config import BoundingBox
from backend.app.data.cache import RedisLiveCache
from backend.app.data.consumers.adsb_history import AsyncpgHistoryWriter, HistoryConsumer
from backend.app.data.consumers.adsb_live import LiveConsumer
from backend.app.data.publisher import KafkaPositionPublisher
from backend.app.data.replay.adsb import build_replay


@pytest.mark.asyncio
async def test_fabricated_replay_end_to_end_against_real_stores():
    replay_id = "e2e-" + uuid.uuid4().hex
    source_id = f"replay:{replay_id}"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    fixture = Path("tests/fixtures/adsb/fabricated_opensky.json")
    plan = build_replay(fixture, replay_id=replay_id, start=now,
        seed=42, bbox=BoundingBox(lamin=20, lomin=70, lamax=30, lomax=80), background_count=0)
    pg_host, pg_port = os.getenv("ADSB_PG_HOST", "127.0.0.1"), os.getenv("ADSB_PG_PORT", "15432")
    redis_host, redis_port = os.getenv("ADSB_REDIS_HOST", "127.0.0.1"), os.getenv("ADSB_REDIS_PORT", "16379")
    brokers = os.getenv("ADSB_KAFKA_BROKERS", "127.0.0.1:19092")
    pool = await asyncpg.create_pool(f"postgresql://garuda:garuda-local-only@{pg_host}:{pg_port}/garuda")
    client = redis.from_url(f"redis://:garuda-redis-local-only@{redis_host}:{redis_port}/0", decode_responses=True)
    cache = RedisLiveCache(client, prefix=f"adsb:{replay_id}")
    history_raw = AIOKafkaConsumer("garuda.adsb.positions", bootstrap_servers=brokers,
        group_id=f"history-{replay_id}", enable_auto_commit=False, auto_offset_reset="latest")
    live_raw = AIOKafkaConsumer("garuda.adsb.positions", bootstrap_servers=brokers,
        group_id=f"live-{replay_id}", enable_auto_commit=False, auto_offset_reset="latest")
    publisher = KafkaPositionPublisher(brokers)
    await history_raw.start(); await live_raw.start(); await publisher.start()
    try:
        for consumer in (history_raw, live_raw):
            for _ in range(100):
                if consumer.assignment():
                    break
                await asyncio.sleep(0.05)
            assert consumer.assignment(), "consumer did not receive a partition assignment"
        await publisher.publish_many(plan.events)
        history, live = HistoryConsumer(history_raw, AsyncpgHistoryWriter(pool)), LiveConsumer(live_raw, cache)
        for _ in plan.events:
            await asyncio.wait_for(history.process_one(), timeout=10)
            await asyncio.wait_for(live.process_one(), timeout=10)
        async with pool.acquire() as connection:
            assert await connection.fetchval("SELECT count(*) FROM adsb.adsb_track WHERE source_id=$1", source_id) == len(plan.events)
            assert await connection.fetchval("SELECT count(*) FROM adsb.grid_occupancy WHERE source_id=$1", source_id) == len(plan.events)
            await connection.execute("SET ROLE garuda_adsb_reader")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute("INSERT INTO adsb.adsb_track(time,source_id,icao24,lat,lon,on_ground,ingested_at) VALUES(now(),'forbidden','000000',0,0,false,now())")
            await connection.execute("RESET ROLE")
        assert await cache.get(plan.events[0].entity_id, now=now) is not None
    finally:
        await publisher.stop(); await history_raw.stop(); await live_raw.stop()
        async with pool.acquire() as connection:
            await connection.execute("DELETE FROM adsb.adsb_track WHERE source_id=$1", source_id)
        await client.delete(cache.geo_key, cache.expiry_key, *[cache.track_key(event.entity_id) for event in plan.events])
        await client.aclose(); await pool.close()
