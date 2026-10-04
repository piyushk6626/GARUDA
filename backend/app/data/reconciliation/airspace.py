from __future__ import annotations
from typing import Any
from ...schemas.airspace import AirspaceEvent
from ..repositories.airspace import AirspaceRepository
class AirspaceReconciler:
    def __init__(self,pool:Any,live:Any|None=None,graph:Any|None=None)->None:self.pool,self.live,self.graph=pool,live,graph
    async def rebuild(self,run_id:str,at:Any)->int:
        zones=await AirspaceRepository(self.pool).get_active_zones(run_id,at); count=0
        for z in zones:
            event=AirspaceEvent.from_zone(z,at=at,active=True,kind="reconciliation")
            if self.live: await self.live.apply(event)
            if self.graph: await self.graph.apply(event)
            count+=1
        return count
