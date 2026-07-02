#!/usr/bin/env python3
"""
build_mrms_masks.py
===================
ONE-TIME (workstation) — precompute which MRMS CONUS 0.01-degree grid cells
intersect each Cullowhee sub-basin, with area weights, so the RUNTIME MRMS
fetcher (ledger/fetch_mrms.py) needs no geo dependencies at all.

WHAT IT DOES
  1. Reads the 8 StreamStats basin GeoJSONs (FeatureCollection holding a
     pour Point + watershed MultiPolygon, WGS84).
  2. Builds the MRMS cell polygons over each basin's bounding box.
     MRMS CONUS grid: 0.01 deg, cell CENTERS at x.xx5 (lon -129.995..-60.005,
     lat 20.005..54.995); a cell's edges lie on the hundredths.
  3. Reprojects basin + cells to NC State Plane ftUS (EPSG:2264) and computes
     intersection-area weights (weights sum to 1 per basin).
  4. Writes ledger/mrms_masks.json:
        {
          "grid": "MRMS CONUS 0.01deg",
          "bbox_wgrib2": {"lon_w":..., "lon_e":..., "lat_s":..., "lat_n":...},
          "basins": { "CC-WCU-2260": {"area_sqmi":..., "n_cells":...,
                       "cells": [{"lat":.., "lon_e":.., "w":..}, ...] }, ... }
        }
     lon_e is 0-360 EAST to match wgrib2 -csv output directly.
     bbox_wgrib2 is the union bbox + margin, ready for `wgrib2 -small_grib`.

RUN
  pip install shapely pyproj
  python scripts/build_mrms_masks.py --geojson-dir /path/to/basin/geojsons \
                                     --out ledger/mrms_masks.json

Commit the resulting mrms_masks.json to the repo — it is small, static, and
the runtime fetchers load it instead of importing shapely/pyproj.
Lives in scripts/ per repo convention: geo deps stay out of the deployed app.
"""

import argparse
import glob
import json
import math
import os
import sys

from shapely.geometry import shape, box
from shapely.ops import transform as shp_transform
from pyproj import Transformer

TARGET_EPSG = 2264            # NAD83 / North Carolina (ftUS) — repo standard
CELL = 0.01                   # MRMS CONUS grid spacing, degrees
SQFT_PER_SQMI = 27_878_400.0
BBOX_MARGIN = 0.03            # deg margin around union bbox for -small_grib

# The eight roster basins; keys must match cullowhee_roster.csv basin_id.
BASIN_FILES = {
    "CC-UP-503":     "CC-UP-503.geojson",
    "CC-MS-1100":    "CC-MS-1100.geojson",
    "CC-TIL-705":    "CC-TIL-705.geojson",
    "CC-SPD-1830":   "CC-SPD-1830.geojson",
    "CC-COX-097":    "CC-COX-097.geojson",
    "CC-LB-171":     "CC-LB-171.geojson",
    "CC-WCU-2260":   "CC-WCU-2260.geojson",
    "CC-MOUTH-2340": "CC-MOUTH-2340.geojson",
}

_T = Transformer.from_crs("EPSG:4326", f"EPSG:{TARGET_EPSG}", always_xy=True)


def _to_sp(geom):
    """WGS84 geometry -> NC State Plane ftUS."""
    return shp_transform(lambda x, y, z=None: _T.transform(x, y), geom)


def load_watershed(path):
    """Return the MultiPolygon watershed geometry from a StreamStats GeoJSON."""
    with open(path) as f:
        gj = json.load(f)
    polys = [shape(ft["geometry"]) for ft in gj.get("features", [])
             if ft["geometry"]["type"] in ("Polygon", "MultiPolygon")]
    if not polys:
        raise ValueError(f"no polygon feature in {path}")
    geom = polys[0]
    for p in polys[1:]:
        geom = geom.union(p)
    return geom


def cell_center(v):
    """Snap a coordinate to the MRMS cell-center lattice (x.xx5)."""
    return math.floor(v / CELL) * CELL + CELL / 2.0


def basin_cells(ws):
    """Yield (lat_center, lon_center_wgs84, weight) for every MRMS cell
    intersecting watershed `ws` (WGS84). Weights sum to 1."""
    ws_sp = _to_sp(ws)
    total = ws_sp.area
    lon0, lat0, lon1, lat1 = ws.bounds
    la = cell_center(lat0)
    out = []
    while la - CELL / 2 <= lat1:
        lo = cell_center(lon0)
        while lo - CELL / 2 <= lon1:
            cell = box(lo - CELL / 2, la - CELL / 2, lo + CELL / 2, la + CELL / 2)
            inter = ws.intersection(cell)
            if not inter.is_empty:
                a = _to_sp(inter).area
                if a > 0:
                    out.append((la, lo, a / total))
            lo += CELL
        la += CELL
    s = sum(w for _, _, w in out)
    return [(la, lo, w / s) for la, lo, w in out], total / SQFT_PER_SQMI


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojson-dir", required=True,
                    help="directory containing the 8 CC-*.geojson files")
    ap.add_argument("--out", default="ledger/mrms_masks.json")
    args = ap.parse_args()

    basins, all_lats, all_lons = {}, [], []
    for bid, fname in BASIN_FILES.items():
        path = os.path.join(args.geojson_dir, fname)
        if not os.path.exists(path):
            hits = glob.glob(os.path.join(args.geojson_dir, fname))
            if not hits:
                print(f"MISSING {fname} — skipping {bid}", file=sys.stderr)
                continue
            path = hits[0]
        ws = load_watershed(path)
        cells, area_sqmi = basin_cells(ws)
        basins[bid] = {
            "area_sqmi": round(area_sqmi, 3),
            "n_cells": len(cells),
            "cells": [{"lat": round(la, 3),
                       "lon_e": round(lo + 360.0, 3),   # 0-360 E for wgrib2 csv
                       "w": round(w, 6)} for la, lo, w in cells],
        }
        all_lats += [la for la, _, _ in cells]
        all_lons += [lo for _, lo, _ in cells]
        print(f"{bid:14s} {area_sqmi:7.2f} mi^2  {len(cells):4d} cells")

    if not basins:
        sys.exit("no basins processed")

    bbox = {
        "lon_w": round(min(all_lons) - BBOX_MARGIN + 360.0, 3),
        "lon_e": round(max(all_lons) + BBOX_MARGIN + 360.0, 3),
        "lat_s": round(min(all_lats) - BBOX_MARGIN, 3),
        "lat_n": round(max(all_lats) + BBOX_MARGIN, 3),
    }
    doc = {"grid": "MRMS CONUS 0.01deg (cell centers at x.xx5)",
           "target_epsg_for_weights": TARGET_EPSG,
           "bbox_wgrib2": bbox,
           "basins": basins}
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(doc, f, indent=1)
    print(f"\nwrote {args.out}")
    print(f"wgrib2 -small_grib {bbox['lon_w']}:{bbox['lon_e']} "
          f"{bbox['lat_s']}:{bbox['lat_n']}")


if __name__ == "__main__":
    main()
