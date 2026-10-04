"""Scheduling works from an injected logical clock, never an implicit wall clock."""
from __future__ import annotations
from datetime import datetime
from typing import Any
from ...schemas.airspace import AirspaceEvent
from ..repositories.airspace import AirspaceRepository
class AirspaceScheduler:
    def __init__(self,pool:Any,service:Any)->None:self.pool,self.service=pool,service
    async def tick(self,run_id:str,clock:datetime)->int:
        repo=AirspaceRepository(self.pool); active={z.zone_id for z in await repo.get_active_zones(run_id,clock)}
        rows=await self.pool.fetch("SELECT * FROM airspace.airspace_zone WHERE run_id=$1 ORDER BY zone_id",run_id); count=0
        for row in rows:
            zone=await repo.get_zone(row["zone_id"])
            if zone is None: continue
            provenance=row["provenance"] if isinstance(row["provenance"],dict) else __import__("json").loads(row["provenance"])
            now=zone.zone_id in active; prior=bool(provenance.get("scheduled_active",not zone.windows))
            if now!=prior:
                await self.service.upsert_zone(zone.model_copy(update={"version":zone.version+1,"provenance":{**zone.provenance,"scheduled_active":now}}),at=clock,idempotency_key=f"schedule:{clock.isoformat()}:{zone.zone_id}",kind="activated" if now else "deactivated"); count+=1
        await self.pool.execute("UPDATE airspace.scenario_run SET logical_clock=$2 WHERE run_id=$1",run_id,clock)
        return count
