"""Rebuild India map fixtures from DataMeet (CC-0) and Natural Earth (public domain).

Usage: python scripts/build_india_fixtures.py SRC_DIR
SRC_DIR holds india.geojson (DataMeet india-composite) and the ne_10m files saved as
railroads, roads, rivers_lake_centerlines, populated_places, admin_1_states_provinces_lines.
"""

import json
import sys
from pathlib import Path

from shapely.geometry import box, mapping, shape
from shapely.ops import linemerge, unary_union

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "gis"
WEST_BOX = box(60.0, 23.8, 77.2, 38.0)  # Sir Creek to Siachen: Pakistan side of the claim


def _round(value):
    if isinstance(value, (int, float)):
        return round(value, 4)
    return [_round(v) for v in value]


def load(src: Path, name: str) -> list[dict]:
    features = json.loads((src / f"{name}.geojson").read_text())["features"]
    return [f for f in features if f.get("geometry")]


def write(name: str, features: list[dict]) -> None:
    payload = {"type": "FeatureCollection", "features": features}
    (OUT / name).write_text(json.dumps(payload, separators=(",", ":")))
    print(name, len(features), (OUT / name).stat().st_size // 1024, "KiB")


def lines_in(geom, region, tol: float) -> list:
    clipped = geom.intersection(region)
    if clipped.is_empty:
        return []
    parts = getattr(clipped, "geoms", [clipped])
    return [p.simplify(tol) for p in parts if p.geom_type == "LineString" and not p.is_empty]


def main(src: Path) -> None:
    raw = unary_union([shape(f["geometry"]).buffer(0) for f in load(src, "india")])
    india = raw.simplify(0.01, preserve_topology=True)
    region = india.buffer(0.05)
    write(
        "india_outline.geojson",
        [{"type": "Feature", "properties": {
            "name": "India",
            "note": "Entire country incl. J&K, Ladakh and island claims per Survey of India. "
                    "Simplified from DataMeet india-composite (CC-0)."},
          "geometry": mapping(india)}],
    )

    # International land border / Line of Control = outline boundary facing Pakistan.
    boundary = unary_union([p.exterior for p in getattr(india, "geoms", [india])])
    pieces = boundary.intersection(WEST_BOX)
    pieces = linemerge(list(getattr(pieces, "geoms", [pieces])))
    west = max(getattr(pieces, "geoms", [pieces]), key=lambda line: line.length)
    write(
        "india_border.geojson",
        [{"type": "Feature",
          "properties": {"border_id": "IN-PK", "name": "India-Pakistan (IB/LoC)", "kind": "international"},
          "geometry": mapping(west)}],
    )

    layers: list[dict] = []
    def add(kind: str, geom, props: dict) -> None:
        layers.append({"type": "Feature", "properties": {"kind": kind, **props}, "geometry": mapping(geom)})

    for f in load(src, "railroads"):
        for g in lines_in(shape(f["geometry"]), region, 0.005):
            add("rail", g, {"name": "Railway"})
    for f in load(src, "roads"):
        p = f["properties"]
        for g in lines_in(shape(f["geometry"]), region, 0.005):
            add("road", g, {"name": p.get("name") or p.get("ref") or "Road", "class": p.get("type") or ""})
    for f in load(src, "rivers_lake_centerlines"):
        for g in lines_in(shape(f["geometry"]), region, 0.005):
            add("river", g, {"name": f["properties"].get("name") or "River"})
    for f in load(src, "admin_1_states_provinces_lines"):
        for g in lines_in(shape(f["geometry"]), india, 0.01):
            add("state", g, {"name": "State boundary"})
    for f in load(src, "populated_places"):
        p = f["properties"]
        pt = shape(f["geometry"])
        if p.get("ADM0_A3") == "IND" or (region.contains(pt) and p.get("SCALERANK", 99) <= 5):
            add("city", pt, {"name": p.get("NAME"), "pop": p.get("POP_MAX") or 0, "rank": p.get("SCALERANK", 9)})
    for f in layers:
        f["geometry"]["coordinates"] = _round(f["geometry"]["coordinates"])
    write("india_layers.geojson", layers)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
