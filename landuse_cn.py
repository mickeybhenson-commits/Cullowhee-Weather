"""
landuse_cn.py  —  Per-sub-basin impervious % and curve number from IMAGERY
==========================================================================
Derives, for each Cullowhee sub-basin, the land-cover fractions, impervious
fraction, and a composite SCS curve number (CN) — straight from imagery-derived
products, with NO county parcel data, CAMA join, or land-use-code crosswalk.

Inputs (all LOCAL files you download once; see DOWNLOAD NOTES at bottom):
  1. sub-basins   : polygons with a basin id field  (your StreamStats KML/GeoJSON/shp)
  2. footprints   : building-footprint polygons       (Microsoft/Google ML footprints)
  3. worldcover   : ESA WorldCover 10 m land-cover GeoTIFF tile covering the watershed

Output: cullowhee_landcover_cn.csv  — one row per basin:
        basin_id, area_mi2, tree%, grass%, crop%, shrub%, bare%, water%,
        builtup%, roof_imperv%, total_imperv%, CN
The CN column drops straight into test_model.BASINS[...]["CN2"].

Run:  python landuse_cn.py
Deps: pip install geopandas rasterio numpy shapely pyproj
"""

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.mask import mask as rio_mask

# ---------------------------------------------------------------------------
# CONFIG — point these at your downloaded files, set the basin id field.
# ---------------------------------------------------------------------------
BASINS_PATH     = "cullowhee_subbasins.geojson"   # your 8 sub-basin polygons
BASIN_ID_FIELD  = "basin_id"                       # attribute holding the name
FOOTPRINTS_PATH = "buildings.geojson"              # ML building footprints (or None)
WORLDCOVER_PATH = "worldcover_cullowhee.tif"       # ESA WorldCover 10 m tile
OUTPUT_CSV      = "cullowhee_landcover_cn.csv"

EQUAL_AREA_EPSG = 5070     # CONUS Albers (meters) — correct for area math
ROOF_MULTIPLIER = 2.0      # roofs are ~half of total impervious in low-density
                           # development; total_imperv ≈ roof_frac * this. Tune.

# CALIBRATION: SCS-CN under-predicts runoff on steep, forested, well-drained
# terrain, so the textbook CNs come out low. Anchor the AREA-WEIGHTED MEAN CN
# to a field-validated value (your back-calculated ~63) and let the imagery set
# the spatial spread around it (developed basins above, pristine below).
#   "offset" preserves cover-driven CN *differences* between basins (recommended
#            for a nearly-all-pervious watershed like this one)
#   "scale"  preserves their *ratios*
TARGET_MEAN_CN = 63.0      # set to None to skip calibration, keep textbook CNs
CALIB_METHOD   = "offset"  # "offset" or "scale"

# Dominant hydrologic soil group fractions for the watershed (from your UP-503
# SSURGO; soils were ~uniform A/B). Override per-basin if you confirm otherwise.
HSG_FRACTIONS = {"A": 0.842, "B": 0.121, "C": 0.029, "D": 0.008}

# ---------------------------------------------------------------------------
# ESA WorldCover class codes -> our cover buckets
# ---------------------------------------------------------------------------
WC_CLASS = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "builtup",
            60: "bare", 70: "snow", 80: "water", 90: "water", 95: "tree", 100: "bare"}

# ---------------------------------------------------------------------------
# SCS curve numbers (TR-55, AMC II) by cover bucket and hydrologic soil group.
# Impervious surfaces are handled separately at CN 98.
# ---------------------------------------------------------------------------
CN_LOOKUP = {
    "tree":  {"A": 30, "B": 55, "C": 70, "D": 77},   # woods, good condition
    "shrub": {"A": 30, "B": 48, "C": 65, "D": 73},   # brush, good
    "grass": {"A": 39, "B": 61, "C": 74, "D": 80},   # pasture/open space, good
    "crop":  {"A": 67, "B": 78, "C": 85, "D": 89},   # row crops, good
    "bare":  {"A": 77, "B": 86, "C": 91, "D": 94},   # bare/fallow
    "water": {"A": 98, "B": 98, "C": 98, "D": 98},   # water/wetland ~ full runoff
    "snow":  {"A": 98, "B": 98, "C": 98, "D": 98},
    # builtup pervious portion (lawns between buildings) ~ open space good
    "builtup_pervious": {"A": 39, "B": 61, "C": 74, "D": 80},
}
IMPERVIOUS_CN = 98


def load_basins(path, id_field):
    gdf = gpd.read_file(path)
    if id_field not in gdf.columns:
        # KML often calls it 'Name'
        for alt in ("Name", "name", "NAME", "id"):
            if alt in gdf.columns:
                gdf = gdf.rename(columns={alt: id_field})
                break
    return gdf.to_crs(epsg=EQUAL_AREA_EPSG)


def cover_fractions(basin_geom, wc_path):
    """Fraction of each cover bucket inside the basin, from WorldCover raster."""
    with rasterio.open(wc_path) as src:
        geom = gpd.GeoSeries([basin_geom], crs=EQUAL_AREA_EPSG).to_crs(src.crs)
        out, _ = rio_mask(src, [geom.iloc[0]], crop=True)
    vals = out[0].ravel()
    vals = vals[vals != src.nodata] if src.nodata is not None else vals
    vals = vals[vals != 0]
    if vals.size == 0:
        return {}
    frac = {}
    total = vals.size
    for code, n in zip(*np.unique(vals, return_counts=True)):
        bucket = WC_CLASS.get(int(code), "bare")
        frac[bucket] = frac.get(bucket, 0.0) + n / total
    return frac


def roof_impervious_fraction(basin_geom, footprints_gdf, basin_area_m2):
    if footprints_gdf is None or basin_area_m2 <= 0:
        return 0.0
    clipped = gpd.clip(footprints_gdf, basin_geom)
    if clipped.empty:
        return 0.0
    return float(clipped.geometry.area.sum() / basin_area_m2)


def composite_cn(frac, roof_imperv, hsg=HSG_FRACTIONS):
    """Area-weighted CN: impervious at 98, pervious covers by HSG."""
    # total impervious: prefer measured roofs * multiplier, but never less than
    # the WorldCover built-up fraction (which already includes some pavement).
    built = frac.get("builtup", 0.0)
    total_imperv = max(min(roof_imperv * ROOF_MULTIPLIER, 0.95), built * 0.6)
    total_imperv = min(total_imperv, 0.95)

    # pervious cover fractions, rescaled to fill (1 - impervious)
    perv = {k: v for k, v in frac.items() if k != "builtup"}
    # builtup's pervious remainder becomes "builtup_pervious"
    builtup_perv = max(built - total_imperv, 0.0)
    if builtup_perv > 0:
        perv["builtup_pervious"] = perv.get("builtup_pervious", 0.0) + builtup_perv
    s = sum(perv.values())
    if s > 0:
        scale = (1.0 - total_imperv) / s
        perv = {k: v * scale for k, v in perv.items()}

    cn = total_imperv * IMPERVIOUS_CN
    for cover, cfrac in perv.items():
        table = CN_LOOKUP.get(cover, CN_LOOKUP["grass"])
        cn_cover = sum(hsg.get(g, 0) * table[g] for g in "ABCD")
        cn += cfrac * cn_cover
    return cn, total_imperv


def calibrate_cn(rows, target_mean=TARGET_MEAN_CN, method=CALIB_METHOD):
    """Anchor the area-weighted mean CN to a validated target, preserving the
    imagery-derived spatial pattern. Adds 'CN_cal' to each row.
    Returns (rows, textbook_mean, adjustment)."""
    if not target_mean:
        for r in rows:
            r["CN_cal"] = r["CN"]
        return rows, None, None
    A = sum(r["area_mi2"] for r in rows) or 1.0
    mean = sum(r["CN"] * r["area_mi2"] for r in rows) / A
    if method == "scale":
        k = target_mean / mean if mean else 1.0
        for r in rows:
            r["CN_cal"] = round(min(max(r["CN"] * k, 30), 98), 1)
        return rows, round(mean, 1), round(k, 3)
    delta = target_mean - mean                      # default: offset
    for r in rows:
        r["CN_cal"] = round(min(max(r["CN"] + delta, 30), 98), 1)
    return rows, round(mean, 1), round(delta, 2)


def main():
    basins = load_basins(BASINS_PATH, BASIN_ID_FIELD)
    footprints = None
    if FOOTPRINTS_PATH:
        try:
            footprints = gpd.read_file(FOOTPRINTS_PATH).to_crs(epsg=EQUAL_AREA_EPSG)
        except Exception as e:
            print(f"(no footprints loaded: {e}; using WorldCover built-up only)")

    rows = []
    for _, b in basins.iterrows():
        geom = b.geometry
        area_m2 = geom.area
        frac = cover_fractions(geom, WORLDCOVER_PATH)
        roof = roof_impervious_fraction(geom, footprints, area_m2)
        cn, timp = composite_cn(frac, roof)
        rows.append({
            "basin_id": b[BASIN_ID_FIELD],
            "area_mi2": round(area_m2 / 2.59e6, 2),
            "tree%":    round(100 * frac.get("tree", 0), 1),
            "grass%":   round(100 * frac.get("grass", 0), 1),
            "crop%":    round(100 * frac.get("crop", 0), 1),
            "shrub%":   round(100 * frac.get("shrub", 0), 1),
            "bare%":    round(100 * frac.get("bare", 0), 1),
            "water%":   round(100 * frac.get("water", 0), 1),
            "builtup%": round(100 * frac.get("builtup", 0), 1),
            "roof_imperv%":  round(100 * roof, 1),
            "total_imperv%": round(100 * timp, 1),
            "CN": round(cn, 1),
        })

    rows, textbook_mean, adj = calibrate_cn(rows)

    import csv, json
    with open(OUTPUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    # cn_overrides.json -> test_model picks this up automatically (calibrated CNs)
    json.dump({r["basin_id"]: r["CN_cal"] for r in rows},
              open("cn_overrides.json", "w"), indent=2)

    print(f"wrote {OUTPUT_CSV} and cn_overrides.json ({len(rows)} basins)")
    if textbook_mean is not None:
        unit = "x" if CALIB_METHOD == "scale" else "+"
        print(f"textbook area-wtd mean CN {textbook_mean} -> anchored to "
              f"{TARGET_MEAN_CN} ({CALIB_METHOD} {unit}{adj})")
    print(f"  {'basin':14s} {'imperv%':>7} {'CN_raw':>7} {'CN_cal':>7}")
    for r in rows:
        print(f"  {r['basin_id']:14s} {r['total_imperv%']:7.1f} "
              f"{r['CN']:7.1f} {r['CN_cal']:7.1f}")


# ===========================================================================
# DOWNLOAD NOTES — get these three local files once, then run.
# ---------------------------------------------------------------------------
# 1. SUB-BASINS: your StreamStats delineations. Merge the 8 KMLs into one
#    GeoJSON with a basin_id column (QGIS: Merge Vector Layers, or geopandas
#    pd.concat). Or run per-KML and concatenate.
#
# 2. BUILDING FOOTPRINTS (imagery-derived, free, no parcels):
#    - Microsoft US Building Footprints: github.com/microsoft/USBuildingFootprints
#      -> download the North Carolina .geojson.zip, clip to the watershed.
#    - or Google Open Buildings / the Microsoft GlobalMLBuildingFootprints repo.
#    Set FOOTPRINTS_PATH=None to skip and lean on WorldCover built-up alone.
#
# 3. ESA WORLDCOVER 10 m (imagery-derived land cover, free):
#    - esa-worldcover.org  ->  download the 3x3 deg tile covering 35.2-35.3 N,
#      83.1-83.3 W (tile ~ S33W084 area). It's a GeoTIFF; clip to watershed.
#    - alt finer/current: Google Dynamic World (10 m, via Earth Engine) or
#      ESRI 10 m Land Cover — swap WC_CLASS codes if you use a different product.
#
# All three are imagery products: building footprints answer "where is the
# impervious" directly (no parcel crosswalk), WorldCover answers "forest vs
# grass vs built" at 10 m (finer than the 30 m NLCD StreamStats gave you).
# ===========================================================================

if __name__ == "__main__":
    main()
