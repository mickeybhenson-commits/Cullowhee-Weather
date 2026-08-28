"""Tag a cluster with the NOAH/SKYE sub-basin it sits in.

The roster is cullowhee_subbasins.geojson in the repo root — the same eight
cumulative drainage areas the flood side of the system uses, so a slope
detection and a stream node can be talked about in one sentence.

Point-in-polygon, smallest containing basin wins: the polygons are cumulative
(Mouth contains everything upstream of it), so without the area tie-break every
cluster would report "Mouth" and the tag would carry no information.
"""
from __future__ import annotations

import json
from pathlib import Path

ROSTER = Path(__file__).resolve().parents[2] / "cullowhee_subbasins.geojson"

OUTSIDE = ("outside roster", "—")


def _rings(geom) -> list[list]:
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon":
        return [poly[0] for poly in geom["coordinates"]]
    return []


def _pip(lon: float, lat: float, ring) -> bool:
    """Ray casting; ring is [[lon, lat], ...]."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def _ring_area(ring) -> float:
    """Unsigned shoelace area in degree² — only used to rank containment."""
    a = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def load_roster(path: Path = ROSTER) -> list[dict]:
    if not Path(path).exists():
        print(f"::warning::sub-basin roster {path} not found — clusters will be untagged")
        return []
    gj = json.loads(Path(path).read_text())
    roster = []
    for f in gj["features"]:
        rings = _rings(f["geometry"])
        if not rings:
            continue
        roster.append(
            {
                "basin_id": f["properties"].get("basin_id", "?"),
                "name": f["properties"].get("name", "?"),
                "rings": rings,
                "area": sum(_ring_area(r) for r in rings),
            }
        )
    roster.sort(key=lambda b: b["area"])       # smallest first
    return roster


def tag(lon: float, lat: float, roster: list[dict]) -> tuple[str, str]:
    """(basin_id, name) of the smallest roster polygon containing the point."""
    for b in roster:                            # already smallest-first
        if any(_pip(lon, lat, r) for r in b["rings"]):
            return b["basin_id"], b["name"]
    return OUTSIDE


def roster_geojson(path: Path = ROSTER) -> dict:
    """The roster as the map page embeds it (geometry + label anchors)."""
    if not Path(path).exists():
        return {"type": "FeatureCollection", "features": []}
    return json.loads(Path(path).read_text())
