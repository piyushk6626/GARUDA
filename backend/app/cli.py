"""Runnable ADS-B commands. Offline replay never reaches OpenSky."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from .config import AdsBSettings
from .data.archive import MinioSnapshotArchive
from .data.cache import RedisLiveCache
from .data.consumers.adsb_history import AsyncpgHistoryWriter, HistoryConsumer
from .data.consumers.adsb_live import LiveConsumer
from .data.ingestors.adsb import OpenSkyClient
from .data.publisher import KafkaPositionPublisher
from .data.replay.adsb import build_replay


async def _replay(args: argparse.Namespace) -> int:
    settings = AdsBSettings()
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    plan = build_replay(Path(args.fixture), replay_id=args.replay_id, start=start, seed=args.seed,
        bbox=settings.bounding_box, background_count=args.background)
    publisher = KafkaPositionPublisher(settings.kafka_bootstrap_servers)
    await publisher.start()
    try: count = await publisher.publish_many(plan.events)
    finally: await publisher.stop()
    print(f"{plan.label}: published {count} events with source_id={plan.source_id}")
    return 0


async def _poll(args: argparse.Namespace) -> int:
    settings = AdsBSettings()
    archive = MinioSnapshotArchive(endpoint=settings.minio_endpoint, access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key.get_secret_value(), bucket=settings.snapshot_bucket)
    archive.initialize()
    publisher, source = KafkaPositionPublisher(settings.kafka_bootstrap_servers), OpenSkyClient(settings)
    await publisher.start()
    try:
        from .service import IngestionService
        service = IngestionService(settings, source, archive, publisher)
        if args.once:
            print(f"published {await service.poll_once()} observed positions")
        else:
            while True:
                await service.poll_once()
                await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        await source.close()
        await publisher.stop()
    return 0


async def _worker(args: argparse.Namespace) -> int:
    import asyncpg
    from aiokafka import AIOKafkaConsumer
    from redis import asyncio as redis
    settings = AdsBSettings()
    consumer = AIOKafkaConsumer("garuda.adsb.positions", bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=f"garuda-adsb-{args.role}", enable_auto_commit=False, auto_offset_reset="earliest")
    await consumer.start()
    pool = None
    try:
        if args.role == "history":
            pool = await asyncpg.create_pool(settings.postgres_dsn, server_settings={"statement_timeout": "10000"})
            worker = HistoryConsumer(consumer, AsyncpgHistoryWriter(pool))
        else:
            client = redis.from_url(settings.redis_url, decode_responses=True)
            worker = LiveConsumer(consumer, RedisLiveCache(client, freshness_seconds=settings.live_freshness_seconds))
        while True:
            await worker.process_one()
    finally:
        if pool:
            await pool.close()
        await consumer.stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="garuda-adsb")
    subs = parser.add_subparsers(required=True)
    replay = subs.add_parser("replay", help="publish a labelled fabricated offline replay")
    replay.add_argument("--fixture", default="tests/fixtures/adsb/fabricated_opensky.json")
    replay.add_argument("--replay-id", required=True)
    replay.add_argument("--start", required=True, help="UTC ISO-8601 replay start")
    replay.add_argument("--seed", type=int, default=42)
    replay.add_argument("--background", type=int, default=0)
    replay.set_defaults(handler=_replay)
    poll = subs.add_parser("poll", help="poll OpenSky, archive first, then publish")
    poll.add_argument("--once", action="store_true")
    poll.set_defaults(handler=_poll)
    worker = subs.add_parser("worker", help="run an independent history or live consumer")
    worker.add_argument("role", choices=("history", "live"))
    worker.set_defaults(handler=_worker)
    args = parser.parse_args()
    return asyncio.run(args.handler(args))
