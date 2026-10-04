"""Logical-clock hourly ticks persisted through the command service."""
from __future__ import annotations
from datetime import timedelta
from typing import Any
from ...schemas.crew import CrewCommand
from ..services.crew import CrewCommandService
class CrewSimulator:
    def __init__(self,pool:Any,service:CrewCommandService|None=None)->None:self.pool,self.service=pool,service or CrewCommandService(pool)
    async def run(self,run_id:str,horizon_hours:int,*,speed:float=60)->int:
        run=await self.pool.fetchrow("SELECT logical_clock FROM crew.scenario_run WHERE run_id=$1",run_id)
        if not run:raise KeyError(run_id)
        now=run["logical_clock"];count=0
        for _ in range(horizon_hours):
            now+=timedelta(hours=1);rows=await self.pool.fetch("SELECT crew_id,version FROM crew.crew WHERE run_id=$1 ORDER BY crew_id",run_id)
            for row in rows:
                await self.service.apply(CrewCommand(crew_id=row["crew_id"],run_id=run_id,event_time=now,kind="tick",idempotency_key=f"tick:{now.isoformat()}",expected_version=row["version"]));count+=1
            await self.pool.execute("UPDATE crew.scenario_run SET logical_clock=$2,lifecycle_status='running' WHERE run_id=$1",run_id,now)
        await self.pool.execute("UPDATE crew.scenario_run SET lifecycle_status='complete' WHERE run_id=$1",run_id);return count
