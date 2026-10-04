"""Small deterministic reservoir/circadian fatigue model for the demo."""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
from ...schemas.crew import DutyInterval, DutyKind

MODEL_VERSION="crew-fatigue-v1"
def fatigue_band(score:float)->str:return "RED" if score>0.7 else "AMBER" if score>=0.4 else "GREEN"
def fatigue_score(intervals:tuple[DutyInterval,...]|list[DutyInterval], at:datetime, *, timezone:str="Asia/Kolkata", prior:float=0.2)->float:
    """Bounded deterministic proxy: duty/time-on-task raises score, sleep reduces it."""
    score=prior
    for item in sorted(intervals,key=lambda x:x.start_at):
        end=min(item.end_at or at,at)
        if end<=item.start_at: continue
        hours=(end-item.start_at).total_seconds()/3600
        if item.kind in (DutyKind.DUTY,DutyKind.FLIGHT): score+=.035*hours
        elif item.kind is DutyKind.SLEEP: score-=.055*hours
        else: score-=.012*hours
    local=at.astimezone(ZoneInfo(timezone)); circadian=.08 if local.hour in (1,2,3,4,5) else 0.0
    return max(0.0,min(1.0,score+circadian))
