from __future__ import annotations
from typing import Any
from ...schemas.crew import CrewEvent
class CrewReconciler:
    def __init__(self,pool:Any,*,live:Any|None=None,graph:Any|None=None)->None:self.pool,self.live,self.graph=pool,live,graph
    async def rebuild(self,run_id:str)->int:
        rows=await self.pool.fetch("SELECT e.envelope FROM crew.crew_event_identity e JOIN (SELECT crew_id,max(version) version FROM crew.crew WHERE run_id=$1 GROUP BY crew_id) x ON x.crew_id=e.crew_id AND x.version=e.version WHERE e.run_id=$1 ORDER BY e.crew_id",run_id);count=0
        for row in rows:
            event=CrewEvent.model_validate_json(row["envelope"]) if isinstance(row["envelope"],str) else CrewEvent.model_validate(row["envelope"])
            if self.live:await self.live.apply(event)
            if self.graph:await self.graph.apply(event)
            count+=1
        return count
