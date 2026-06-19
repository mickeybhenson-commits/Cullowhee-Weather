"""
merge_subbasins.py  —  stitch the 8 StreamStats KMLs into one GeoJSON
=====================================================================
Reads each StreamStats sub-basin delineation (KML), extracts the BASIN POLYGON
(ignoring the pour-point marker), tags it with the matching basin_id, merges all
eight into a single file, and sanity-checks each delineated area against the
roster drainage area so you catch a mis-assigned file before it reaches the engine.

Output: cullowhee_subbasins.geojson  (field: basin_id)  — the input that
        landuse_cn.py expects, with ids that match test_model.BASINS keys.

Run:  python merge_subbasins.py
Deps: pip install geopandas shapely pyproj   (no GDAL KML driver needed — KML is
      parsed directly, so it works regardless of your geopandas/fiona build)
"""

import re
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon

# ---------------------------------------------------------------------------
# EDIT THIS: map each StreamStats KML file -> the basin_id it represents.
# The ids MUST match the keys in test_model.BASINS so the CNs bind later.
# (Filenames below are placeholders — drop in your actual eight.)
# ---------------------------------------------------------------------------
FILES = {
    "up503.kml":   "CC-UP-503",
    "ms1100.kml":  "CC-MS-1100",
    "til705.kml":  "CC-TIL-705",
    "spd1830.kml": "CC-SPD-1830",
    "cox097.kml":  "CC-COX-097",
    "lb171.kml":   "CC-LB-171",
    "wcu2260.kml": "CC-WCU-2260",
    "mouth2340.kml": "CC-MOUTH-2340",
}
OUTPUT = "cullowhee_subbasins.geojson"

# expected drainage areas (mi²) from the roster — used only to flag a mismatch
ROSTER_DA = {
    "CC-UP-503": 5.35, "CC-MS-1100": 11.03, "CC-TIL-705": 7.05, "CC-SPD-1830": 18.3,
    "CC-COX-097": 0.97, "CC-LB-171": 1.71, "CC-WCU-2260": 22.6, "CC-MOUTH-2340": 23.4,
}
AREA_TOLERANCE = 0.15   # warn if delineated area is off by more than ±15%


def _parse_coords(s):
    pts = []
    for tok in s.split():
        parts = tok.split(",")
        if len(parts) >= 2:
            try:
                pts.append((float(parts[0]), float(parts[1])))  # lon, lat
            except ValueError:
                pass
    return pts


def read_kml_polygon(path):
    """Extract the basin polygon from a StreamStats KML (largest polygon;
    pour-point <Point> markers are ignored). Returns a shapely geometry in
    EPSG:4326, or None."""
    txt = open(path, encoding="utf-8", errors="ignore").read()
    polys = []
    for block in re.findall(r"<Polygon.*?</Polygon>", txt, re.S | re.I):
        m = re.search(r"<outerBoundaryIs>.*?<coordinates>(.*?)</coordinates>",
                      block, re.S | re.I) or \
            re.search(r"<coordinates>(.*?)</coordinates>", block, re.S | re.I)
        if not m:
            continue
        outer = _parse_coords(m.group(1))
        if len(outer) < 4:
            continue
        holes = [_parse_coords(h) for h in
                 re.findall(r"<innerBoundaryIs>.*?<coordinates>(.*?)</coordinates>",
                            block, re.S | re.I)]
        holes = [h for h in holes if len(h) >= 4]
        polys.append(Polygon(outer, holes))
    if not polys:
        return None
    polys.sort(key=lambda p: p.area, reverse=True)   # basin = largest polygon
    return polys[0]


def main():
    records = []
    for path, bid in FILES.items():
        geom = None
        try:
            geom = read_kml_polygon(path)
        except FileNotFoundError:
            print(f"  ! {path} not found — skipping {bid}")
            continue
        if geom is None:
            print(f"  ! no polygon found in {path} ({bid}) — skipping")
            continue
        records.append({"basin_id": bid, "geometry": geom})

    if not records:
        print("No basins read. Edit the FILES mapping to point at your KMLs.")
        return

    gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")

    # area sanity-check in an equal-area CRS
    eq = gdf.to_crs(epsg=5070)
    print(f"{'basin_id':14s} {'area_mi2':>8} {'roster':>7}  check")
    for i, row in gdf.iterrows():
        a = eq.geometry.iloc[i].area / 2.59e6
        exp = ROSTER_DA.get(row["basin_id"])
        flag = ""
        if exp:
            off = abs(a - exp) / exp
            flag = "OK" if off <= AREA_TOLERANCE else f"** off {off*100:.0f}% — check file mapping"
        print(f"{row['basin_id']:14s} {a:8.2f} {exp if exp else '-':>7}  {flag}")

    gdf.to_file(OUTPUT, driver="GeoJSON")
    print(f"\nwrote {OUTPUT} ({len(gdf)} basins) — ready for landuse_cn.py")


if __name__ == "__main__":
    main()
