"""LangGraph skeletons for the data tools.

Each graph is `query -> shape`. `shape` always emits that tool's fixed output.
"""

from garuda.tools.adapters.langgraph import compile_tool_graph
from garuda.tools.data.get_aircraft_availability import STUB_OUTPUT as AIRCRAFT_OUTPUT
from garuda.tools.data.get_airspace import STUB_OUTPUT as AIRSPACE_OUTPUT
from garuda.tools.data.get_civil_traffic import STUB_OUTPUT as TRAFFIC_OUTPUT
from garuda.tools.data.get_crew_status import STUB_OUTPUT as CREW_OUTPUT
from garuda.tools.data.get_mission_requests import STUB_OUTPUT as MISSION_OUTPUT
from garuda.tools.data.get_threats_in_area import STUB_OUTPUT as THREAT_OUTPUT
from garuda.tools.data.get_weapon_stock import STUB_OUTPUT as WEAPON_OUTPUT
from garuda.tools.data.get_weather_along_route import STUB_OUTPUT as WEATHER_OUTPUT
from garuda.tools.data.query_ontology import STUB_OUTPUT as ONTOLOGY_OUTPUT
from garuda.tools.data.search_doctrine import STUB_OUTPUT as DOCTRINE_OUTPUT
from garuda.tools.data.spatial_query import STUB_OUTPUT as SPATIAL_OUTPUT

AIRCRAFT_GRAPH = compile_tool_graph(steps=("query", "shape"), output=AIRCRAFT_OUTPUT)
CREW_GRAPH = compile_tool_graph(steps=("query", "shape"), output=CREW_OUTPUT)
WEAPON_GRAPH = compile_tool_graph(steps=("query", "shape"), output=WEAPON_OUTPUT)
AIRSPACE_GRAPH = compile_tool_graph(steps=("query", "shape"), output=AIRSPACE_OUTPUT)
WEATHER_GRAPH = compile_tool_graph(steps=("query", "shape"), output=WEATHER_OUTPUT)
THREAT_GRAPH = compile_tool_graph(steps=("query", "shape"), output=THREAT_OUTPUT)
MISSION_GRAPH = compile_tool_graph(steps=("query", "shape"), output=MISSION_OUTPUT)
TRAFFIC_GRAPH = compile_tool_graph(steps=("query", "shape"), output=TRAFFIC_OUTPUT)
ONTOLOGY_GRAPH = compile_tool_graph(steps=("query", "shape"), output=ONTOLOGY_OUTPUT)
SPATIAL_GRAPH = compile_tool_graph(steps=("query", "shape"), output=SPATIAL_OUTPUT)
DOCTRINE_GRAPH = compile_tool_graph(steps=("query", "shape"), output=DOCTRINE_OUTPUT)

GRAPHS = {
    "get_aircraft_availability": AIRCRAFT_GRAPH,
    "get_crew_status": CREW_GRAPH,
    "get_weapon_stock": WEAPON_GRAPH,
    "get_airspace": AIRSPACE_GRAPH,
    "get_weather_along_route": WEATHER_GRAPH,
    "get_threats_in_area": THREAT_GRAPH,
    "get_mission_requests": MISSION_GRAPH,
    "get_civil_traffic": TRAFFIC_GRAPH,
    "query_ontology": ONTOLOGY_GRAPH,
    "spatial_query": SPATIAL_GRAPH,
    "search_doctrine": DOCTRINE_GRAPH,
}
