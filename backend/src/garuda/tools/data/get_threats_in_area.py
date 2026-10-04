"""Data tool `get_threats_in_area`. Backing: NEW queries/threats.py. See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel, Field

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.tools.stubs import fixed


class ThreatItem(BaseModel):
    threat_id: str
    type: str
    lat: float
    lon: float
    confidence: float


class GetThreatsInAreaInput(BaseModel):
    center: tuple[float, float] | None = None
    radius_km: float | None = None
    polygon: list[tuple[float, float]] | None = None
    min_confidence: float = 0.3
    types: list[str] | None = None


class GetThreatsInAreaOutput(BaseModel):
    total: int
    items: list[ThreatItem] = Field(default_factory=list)


SPEC = ToolSpec(
    name="get_threats_in_area",
    description="Threats inside a polygon or radius. Use before routing; not for anomaly detection.",
    input_model=GetThreatsInAreaInput,
    output_model=GetThreatsInAreaOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = GetThreatsInAreaOutput(
    total=1,
    items=[ThreatItem(threat_id="TH-001", type="SAM_MED", lat=27.1, lon=78.4, confidence=0.8)],
)


async def get_threats_in_area(payload: GetThreatsInAreaInput, ctx: ToolContext) -> GetThreatsInAreaOutput:
    """Skeleton: same output for every input until the query is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)
