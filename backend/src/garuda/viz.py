"""Write one self-contained HTML page that visualizes every generated domain."""

import argparse
import json
from collections import Counter
from pathlib import Path

from shapely import wkt
from shapely.geometry import mapping

from garuda.config import load_config
from garuda.generators.missions import build_scenario
from garuda.schemas.mission import GeneratedScenario
from garuda.storage.seed import scenario_counts

TEMPLATE = Path(__file__).with_name("viz_template.html")


def _geo(text: str | None) -> dict | None:
    return mapping(wkt.loads(text)) if text else None


def _rows(items: list, *fields: str) -> list[dict]:
    return [{name: getattr(item, name) for name in fields} for item in items]


def scenario_payload(scenario: GeneratedScenario) -> dict:
    first_hour = min(cell.time for cell in scenario.cells)
    weather = [
        [cell.lat, cell.lon, round(cell.flyability, 2), cell.cb_flag]
        for cell in scenario.cells
        if cell.time == first_hour
    ]
    crew = scenario.crew
    payload = {
        "seed": scenario.seed,
        "counts": scenario_counts(scenario),
        "bases": [b.model_dump() for b in scenario.bases],
        "borders": [{"name": b.name, "geometry": _geo(b.wkt)} for b in scenario.borders],
        "features": [
            {"kind": f.kind, "name": f.name, "geometry": _geo(f.wkt)} for f in scenario.features
        ],
        "zones": [
            {**z.model_dump(mode="json", exclude={"wkt"}), "geometry": _geo(z.wkt)}
            for z in scenario.zones
        ],
        "threats": [t.model_dump(mode="json") for t in scenario.threats],
        "tracks": _rows(scenario.tracks, "threat_id", "lat", "lon"),
        "targets": [t.model_dump(mode="json") for t in scenario.targets],
        "missions": [
            {**m.model_dump(mode="json", exclude={"area_wkt"}), "area": _geo(m.area_wkt)}
            for m in scenario.missions
        ],
        "weather": weather,
        "weather_time": first_hour.isoformat(),
        "observations": [o.model_dump(mode="json") for o in scenario.observations[:60]],
        "adsb": [t.model_dump(mode="json") for t in scenario.adsb_tracks],
        "aircraft": [a.model_dump(mode="json") for a in scenario.aircraft],
        "crew": [
            {
                "crew_id": c.crew_id, "callsign": c.callsign, "role": c.role, "base_id": c.base_id,
                "primary_type": c.primary_type, "medical_status": c.medical_status,
                "fatigue_score": round(c.fatigue_score, 3), "duty_hours_24h": c.duty_hours_24h,
                "is_flight_lead": c.is_flight_lead,
            }
            for c in crew
        ],
        "weapons": [w.model_dump(mode="json") for w in scenario.weapons],
        "stocks": [s.model_dump(mode="json") for s in scenario.stocks],
        "load_configs": [c.model_dump(mode="json") for c in scenario.load_configs],
        "summary": {
            "aircraft_status": Counter(a.status for a in scenario.aircraft),
            "crew_medical": Counter(c.medical_status for c in crew),
            "mission_types": Counter(m.type for m in scenario.missions),
        },
    }
    return payload


def render_html(scenario: GeneratedScenario) -> str:
    data = json.dumps(scenario_payload(scenario), default=str).replace("</", "<\\/")
    return TEMPLATE.read_text().replace("__DATA__", data)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write the GARUDA data visualization page")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--out", type=Path, default=Path("data/viz/index.html"))
    args = parser.parse_args(argv)
    config = load_config(args.config)
    scenario = build_scenario(config, config.seed if args.seed is None else args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_html(scenario))
    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
