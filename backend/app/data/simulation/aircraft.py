"""Small deterministic aircraft-only simulator; wall clock never drives state."""
from __future__ import annotations
import math
from datetime import datetime, timedelta
from typing import Any
import numpy as np
import simpy
from ...schemas.aircraft import AircraftCommand, AircraftStatus

def hazard_probability(hours_since_inspection:float, cycle_hours:float, dt_hours:float, *, surge:float=1.0)->float:
    """Flight-hour wear hazard, converted so tick duration does not alter the rate."""
    rate=(0.00015 + 0.0015 * min(1.0,hours_since_inspection/cycle_hours)) * surge
    return 1-math.exp(-rate*dt_hours)

class LogicalAircraftSimulator:
    def __init__(self,service:Any,start_at:datetime,seed:int,tick_minutes:int=15)->None:
        self.service,self.start_at,self.tick_minutes=service,start_at,tick_minutes; self.env=simpy.Environment(); self.rng=np.random.default_rng(np.random.SeedSequence(seed,spawn_key=(3,)))
    def at(self)->datetime: return self.start_at+timedelta(minutes=self.env.now)
    async def fuel_tick(self,run_id:str,tail_no:str,fuel_kg:float)->None:
        await self.service.apply(AircraftCommand(tail_no=tail_no,run_id=run_id,event_time=self.at(),kind="fuel",idempotency_key=f"tick:{tail_no}:{self.env.now}",fuel_kg=max(0,fuel_kg)))
    async def inject_nmc(self,run_id:str,tail_no:str,reason:str="scripted_aircraft_nmc")->None:
        await self.service.apply(AircraftCommand(tail_no=tail_no,run_id=run_id,event_time=self.at(),kind="injection",idempotency_key=f"inject:{tail_no}:{self.env.now}",status=AircraftStatus.NMC,reason=reason,maint_return_eta=self.at()+timedelta(hours=18)))
    def run_to(self,hours:float)->None: self.env.run(until=hours*60)
