#!/usr/bin/env python3
"""
noah_landcover_watch.py — monthly land-surface change watch for Cullowhee Creek.

WHAT THIS IS FOR, AND WHAT IT DELIBERATELY IS NOT

  It detects patches of land-surface change inside each sub-basin and writes
  them to a ledger. It does NOT change any model parameter. See
  claude/noah_cn_sensitivity_and_imagery_cadence_2026-08-10.md: moving a
  tributary up one posture category takes +20 to +22 curve-number points, and
  realistic land-use drift is under 1 point per year. A monthly job that wrote
  CN automatically would be writing noise into a load-bearing parameter, which
  is the same failure shape as the live.html scenario fallback. So:

      monthly  -> detect patches           (this script, automatic)
      quarterly-> classify what they are   (human, ~20 min, Planet basemaps)
      annual   -> propose a CN delta       (gated, signed off, then basins.py)

TWO DESIGN RULES THAT ARE NOT NEGOTIABLE

  1. Compare the SAME MONTH ONE YEAR EARLIER, never the previous month. This is
     a deciduous Appalachian watershed; month-to-month NDVI is phenology and it
     swamps every real signal by an order of magnitude.
  2. A patch is only reported once it has PERSISTED across two consecutive
     monthly runs. One month of cloud shadow, haze or a bad SCL mask looks
     exactly like a clearing; two in a row does not.

INPUT   the INCREMENTAL (disjoint) sub-basin polygons. Do not point this at the
        cumulative StreamStats delineations — they nest, and a headwater pixel
        would be counted in up to five basins.

USAGE   python noah_landcover_watch.py --month 2026-07
        python noah_landcover_watch.py --selftest     # no network needed
"""

import argparse, csv, json, os, sys, datetime as dt
from collections import defaultdict

import numpy as np

# ---------------------------------------------------------------- configuration
COLLECTION      = "sentinel-2-l2a"
STAC_URL        = "https://planetarycomputer.microsoft.com/api/stac/v1"
TARGET_EPSG     = 32617          # UTM 17N — the zone this watershed sits in
PIXEL_M         = 10.0           # Sentinel-2 B04/B08 native resolution
MAX_CLOUD_PCT   = 60             # per-scene prefilter; SCL does the real masking

NDVI_DROP       = -0.15          # a drop this large is a candidate
# The 0.5 ha floor used until 2026-08-11 rejected the geometry it was meant to
# find. Debris tracks and channel scour are 10-30 m wide: 1-3 pixels. Demanding
# 50 contiguous pixels means a scar ~500 m long AND unbroken, and real scars
# break on shadow, water and mixed pixels. Proven by the Helene control, which
# found fewer patches than a quiet July. Floor is now just above speckle; the
# signal is total affected area and count, not blob size.
# 6 pixels. Low enough to keep a 3-px-wide scar segment (a 4x3 block is 12 px),
# high enough that a 2x2 speck does not become a patch.
MIN_PATCH_HA    = 0.06
PERSISTENCE_N   = 2              # consecutive monthly runs a patch must survive
MIN_VALID_FRAC  = 0.30           # skip a basin if <30% of its pixels are clear

# Sentinel-2 scene classification values we accept as clear ground.
# 4 vegetation, 5 not-vegetated, 6 water, 7 unclassified.
# Explicitly rejected: 3 cloud shadow, 8/9 cloud med/high prob, 10 cirrus,
# 11 snow, 1 saturated, 2 dark area, 0 nodata.
SCL_KEEP = (4, 5, 6, 7)


# ------------------------------------------------------- pure, testable helpers
def scl_valid_mask(scl):
    """Boolean mask of pixels usable for NDVI."""
    return np.isin(scl, SCL_KEEP)


def ndvi(red, nir):
    """NDVI with a guarded denominator. Returns NaN where the sum is ~0."""
    red = red.astype("float32")
    nir = nir.astype("float32")
    den = nir + red
    out = np.full(red.shape, np.nan, dtype="float32")
    ok = np.abs(den) > 1e-6
    out[ok] = (nir[ok] - red[ok]) / den[ok]
    return out


def composite(ndvi_stack, valid_stack):
    """Per-pixel median NDVI over the month, ignoring masked observations.

    Median rather than mean: one undetected cloud edge shifts a mean and barely
    moves a median. Also returns the observation count per pixel, which is the
    honest measure of how much a pixel's value can be trusted.
    """
    import warnings
    arr = np.where(valid_stack, ndvi_stack, np.nan)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN pixels are expected
        med = np.nanmedian(arr, axis=0).astype("float32")
    n = np.sum(valid_stack & np.isfinite(ndvi_stack), axis=0).astype("int16")
    med[n == 0] = np.nan
    return med, n


def zonal_stats(values, basin_index, n_basins):
    """Mean / median / valid-fraction of `values` inside each basin.

    basin_index: int array, -1 outside every basin, else the basin's position.
    """
    out = []
    for i in range(n_basins):
        sel = basin_index == i
        total = int(sel.sum())
        v = values[sel]
        good = np.isfinite(v)
        ngood = int(good.sum())
        frac = (ngood / total) if total else 0.0
        out.append({
            "pixels": total,
            "valid_pixels": ngood,
            "valid_frac": round(frac, 4),
            "ndvi_mean": round(float(np.mean(v[good])), 4) if ngood else None,
            "ndvi_median": round(float(np.median(v[good])), 4) if ngood else None,
        })
    return out


def detect_patches(now, base, basin_index, basin_ids, pixel_m=PIXEL_M,
                   drop=NDVI_DROP, min_ha=MIN_PATCH_HA):
    """Connected components where NDVI fell by at least `drop` year-over-year.

    Only pixels valid in BOTH epochs are considered — a pixel that was clouded
    last July cannot testify about this July.
    """
    from scipy import ndimage

    comparable = np.isfinite(now) & np.isfinite(base)
    delta = np.where(comparable, now - base, np.nan)
    hit = comparable & (delta <= drop) & (basin_index >= 0)

    labels, nlab = ndimage.label(hit)
    px_ha = (pixel_m * pixel_m) / 10000.0
    min_px = max(1, int(round(min_ha / px_ha)))

    patches = []
    for lab in range(1, nlab + 1):
        sel = labels == lab
        npx = int(sel.sum())
        if npx < min_px:
            continue
        ys, xs = np.nonzero(sel)
        # assign the patch to whichever basin holds most of it
        bidx = basin_index[sel]
        vals, counts = np.unique(bidx[bidx >= 0], return_counts=True)
        home = basin_ids[int(vals[np.argmax(counts)])]
        h = ys.max() - ys.min() + 1
        w_ = xs.max() - xs.min() + 1
        long_m = max(h, w_) * pixel_m
        patches.append({
            "basin_id": home,
            "area_ha": round(npx * px_ha, 3),
            "pixels": npx,
            "long_axis_m": int(round(long_m)),
            # >3 means markedly linear — the signature of a scoured corridor or a
            # debris track, as opposed to the blocky footprint of a clearing
            "elongation": round(max(h, w_) / max(1, min(h, w_)), 2),
            "ndvi_drop_mean": round(float(np.nanmean(delta[sel])), 4),
            "ndvi_drop_min": round(float(np.nanmin(delta[sel])), 4),
            "row_centroid": int(round(ys.mean())),
            "col_centroid": int(round(xs.mean())),
        })
    patches.sort(key=lambda p: -p["area_ha"])
    return patches


def patch_key(p, grid=5):
    """Coarse spatial key so the same patch matches across months despite a few
    pixels of wobble at its edges. grid=5 -> 50 m cells at 10 m pixels."""
    return (p["basin_id"], p["row_centroid"] // grid, p["col_centroid"] // grid)


def apply_persistence(current, previous, need=PERSISTENCE_N):
    """Mark patches that have now been seen `need` consecutive months.

    `previous` is last month's patch list (already carrying its own streak).
    A patch absent this month simply drops out — no decay, no memory.
    """
    prev = {patch_key(p): p.get("streak", 1) for p in previous}
    confirmed, provisional = [], []
    for p in current:
        p = dict(p)
        p["streak"] = prev.get(patch_key(p), 0) + 1
        (confirmed if p["streak"] >= need else provisional).append(p)
    return confirmed, provisional


def prev_month(ym):
    y, m = (int(x) for x in ym.split("-"))
    return f"{y-1}-12" if m == 1 else f"{y}-{m-1:02d}"


def year_earlier(ym):
    y, m = (int(x) for x in ym.split("-"))
    return f"{y-1}-{m:02d}"


# ------------------------------------------------------------- geometry helpers
def apply_corridor(basin_index, corridor_path, shape, transform):
    """Restrict detection to a riparian corridor.

    For EVENT response only. Flood damage is confined to the channel corridor,
    and inside a ~100 m buffer the damaged fraction is perhaps 30% rather than
    the 1.6% it is basin-wide — an order of magnitude of signal-to-noise for the
    cost of a geometry intersect. NOT used for drift monitoring: development and
    harvest are not riparian-confined, and masking would hide them.
    """
    import rasterio.features
    from shapely.geometry import shape as shp
    gj = json.load(open(corridor_path, encoding="utf-8"))
    geoms = [shp(f["geometry"]).buffer(0) for f in gj["features"]]
    mask = rasterio.features.rasterize(
        [(g, 1) for g in geoms], out_shape=shape, transform=transform,
        fill=0, dtype="uint8").astype(bool)
    out = basin_index.copy()
    out[~mask] = -1
    kept = int((out >= 0).sum()); was = int((basin_index >= 0).sum())
    print(f"corridor mask: {kept:,} of {was:,} pixels retained "
          f"({kept/max(1,was)*100:.1f}%)")
    return out


def build_basin_index(geojson_path, bounds, shape, transform):
    """Rasterise the sub-basins onto the analysis grid.

    Returns (index_array, basin_ids). Refuses to run on nested polygons.
    """
    import rasterio.features
    from shapely.geometry import shape as shp
    import pyproj
    from shapely.ops import transform as shp_transform

    gj = json.load(open(geojson_path, encoding="utf-8"))
    to_utm = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{TARGET_EPSG}",
                                         always_xy=True).transform
    ids, geoms = [], []
    for f in gj["features"]:
        ids.append(f["properties"].get("id") or f["properties"]["basin_id"])
        geoms.append(shp_transform(to_utm, shp(f["geometry"])).buffer(0))

    # guard: the cumulative delineations nest, and would silently double-count
    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            ov = geoms[i].intersection(geoms[j]).area
            if ov > 0.02 * min(geoms[i].area, geoms[j].area):
                raise SystemExit(
                    f"ERROR: {ids[i]} and {ids[j]} overlap by "
                    f"{ov/min(geoms[i].area, geoms[j].area)*100:.0f}%. These look like the "
                    "CUMULATIVE StreamStats polygons. Use the INCREMENTAL file.")

    idx = rasterio.features.rasterize(
        [(g, i + 1) for i, g in enumerate(geoms)],
        out_shape=shape, transform=transform, fill=0, dtype="int32") - 1
    return idx, ids


# ---------------------------------------------------------------- network layer
def fetch_month(bbox_wgs84, ym, grid):
    """Cloud-masked monthly NDVI composite. Requires network."""
    import planetary_computer as pc
    import rasterio
    from pystac_client import Client
    from rasterio.vrt import WarpedVRT

    y, m = (int(x) for x in ym.split("-"))
    start = dt.date(y, m, 1)
    end = dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1) - dt.timedelta(days=1)

    cat = Client.open(STAC_URL, modifier=pc.sign_inplace)
    items = list(cat.search(collections=[COLLECTION], bbox=bbox_wgs84,
                            datetime=f"{start}/{end}",
                            query={"eo:cloud_cover": {"lt": MAX_CLOUD_PCT}}).items())
    if not items:
        return None, None, 0

    shape, transform = grid
    nd, vl = [], []
    for it in items:
        bands = {}
        for key, asset in (("red", "B04"), ("nir", "B08"), ("scl", "SCL")):
            with rasterio.open(it.assets[asset].href) as src:
                with WarpedVRT(src, crs=f"EPSG:{TARGET_EPSG}", transform=transform,
                               width=shape[1], height=shape[0],
                               resampling=rasterio.enums.Resampling.nearest) as vrt:
                    bands[key] = vrt.read(1)
        nd.append(ndvi(bands["red"], bands["nir"]))
        vl.append(scl_valid_mask(bands["scl"]))
    med, n = composite(np.stack(nd), np.stack(vl))
    return med, n, len(items)


# ----------------------------------------------------------------------- selftest
def selftest():
    """Prove the logic without touching the network."""
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + extra) if extra else ''}")

    print("SCL masking")
    scl = np.array([[4, 5, 6, 7], [3, 8, 9, 10], [0, 1, 2, 11]])
    check("keeps 4/5/6/7 only", scl_valid_mask(scl).sum() == 4)

    print("NDVI")
    v = ndvi(np.array([[100.0]]), np.array([[300.0]]))
    check("(300-100)/(300+100) = 0.5", abs(v[0, 0] - 0.5) < 1e-6)
    check("guards zero denominator", np.isnan(ndvi(np.zeros((1, 1)), np.zeros((1, 1))))[0, 0])

    print("monthly composite")
    st = np.array([[[0.8, 0.8]], [[0.1, 0.8]], [[0.8, 0.8]]], dtype="float32")   # 3 obs
    vs = np.array([[[True, True]], [[True, True]], [[True, True]]])
    med, n = composite(st, vs)
    check("median rejects one cloud-contaminated observation", abs(med[0, 0] - 0.8) < 1e-6,
          f"got {med[0,0]:.3f}")
    check("observation count reported", n[0, 0] == 3)
    med2, n2 = composite(st, np.zeros_like(vs, dtype=bool))
    check("all-masked pixel is NaN, not 0", np.isnan(med2[0, 0]) and n2[0, 0] == 0)

    print("patch detection (100x100 grid, 10 m pixels = 1 ha per 100 px)")
    base = np.full((100, 100), 0.80, dtype="float32")
    now = base.copy()
    now[10:30, 10:30] = 0.30           # 20x20 px = 400 px = 4.0 ha, drop 0.50
    now[60:62, 60:62] = 0.30           # 2x2 px = 0.04 ha — must be rejected
    bidx = np.zeros((100, 100), dtype="int32")
    bidx[:, 50:] = 1
    p = detect_patches(now, base, bidx, ["A", "B"])
    check("finds exactly one patch above the size floor", len(p) == 1, f"found {len(p)}")
    check("area is 4.0 ha", p and abs(p[0]["area_ha"] - 4.0) < 1e-6, f"{p[0]['area_ha'] if p else '-'} ha")
    check("assigned to the basin containing it", p and p[0]["basin_id"] == "A")
    check("mean drop is -0.50", p and abs(p[0]["ndvi_drop_mean"] + 0.50) < 1e-3)

    print("LINEAR SCAR — the Helene-control regression")
    # 3 px wide x 30 px long = 90 px = 0.9 ha, but only 3 pixels across: the
    # shape a debris track or scoured corridor actually makes. The old 0.5 ha
    # floor passed this one; what it rejected was the BROKEN version below,
    # which is what shadow and standing water leave behind.
    b2=np.full((100,100),0.80,dtype="float32"); n2=b2.copy()
    n2[20:50,40:43]=0.30
    bidx2=np.zeros((100,100),dtype="int32")
    lin=detect_patches(n2,b2,bidx2,["A"])
    check("finds a 3-px-wide, 300 m-long scar", len(lin)==1, f"found {len(lin)}")
    check("reports it as linear (elongation >= 8)", lin and lin[0]["elongation"]>=8,
          f"elongation {lin[0]['elongation'] if lin else '-'}")
    check("reports long-axis length in metres", lin and lin[0]["long_axis_m"]==300,
          f"{lin[0]['long_axis_m'] if lin else '-'} m")
    # broken into 5 segments by shadow — total area unchanged, no blob is big
    n3=b2.copy()
    for k in range(5): n3[20+k*6:24+k*6,40:43]=0.30
    seg=detect_patches(n3,b2,bidx2,["A"])
    tot=sum(p["area_ha"] for p in seg)
    check("a scar broken into 5 segments still registers", len(seg)==5, f"found {len(seg)}")
    check("total affected area is preserved (0.60 ha)", abs(tot-0.60)<1e-6, f"{tot:.2f} ha")
    check("OLD 0.5 ha floor would have rejected every segment",
          all(p["area_ha"]<0.5 for p in seg),
          f"largest segment {max(p['area_ha'] for p in seg):.2f} ha")

    print("speckle is still rejected")
    n4=b2.copy(); n4[70,70]=0.30; n4[75,75]=0.30; n4[80:82,80]=0.30
    check("1-2 px specks do not become patches", len(detect_patches(n4,b2,bidx2,["A"]))==0)

    print("patch detection ignores pixels clouded in either epoch")
    b2 = base.copy(); b2[10:30, 10:30] = np.nan
    check("no patch when the baseline is masked there", len(detect_patches(now, b2, bidx, ["A", "B"])) == 0)

    print("persistence")
    c1, p1 = apply_persistence(p, [])
    check("first sighting is provisional, not confirmed", len(c1) == 0 and len(p1) == 1)
    c2, p2 = apply_persistence(p, p1)
    check("second consecutive sighting confirms it", len(c2) == 1 and c2[0]["streak"] == 2)
    moved = [dict(p[0], row_centroid=p[0]["row_centroid"] + 2)]
    c3, _ = apply_persistence(moved, p1)
    check("a few pixels of edge wobble still matches", len(c3) == 1)
    c4, p4 = apply_persistence([dict(p[0], row_centroid=95, col_centroid=5)], p1)
    check("a genuinely different location does not inherit a streak", len(c4) == 0)

    print("zonal stats")
    vals = np.where(bidx == 0, 0.5, 0.9).astype("float32")
    vals[0, 0] = np.nan
    z = zonal_stats(vals, bidx, 2)
    check("mean per basin correct", abs(z[0]["ndvi_mean"] - 0.5) < 1e-6 and abs(z[1]["ndvi_mean"] - 0.9) < 1e-6)
    check("valid fraction accounts for the NaN", z[0]["valid_pixels"] == z[0]["pixels"] - 1)

    print("date helpers")
    check("year_earlier(2026-07) = 2025-07", year_earlier("2026-07") == "2025-07")
    check("prev_month(2026-01) = 2025-12", prev_month("2026-01") == "2025-12")

    print("\n" + ("ALL CHECKS PASSED" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


# --------------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", help="YYYY-MM to analyse (drift mode)")
    ap.add_argument("--event", nargs=2, metavar=("BEFORE","AFTER"),
                    help="EVENT mode: two YYYY-MM months, e.g. --event 2024-08 2024-10. "
                         "Compares them directly instead of same-month-year-earlier. "
                         "Use with --corridor.")
    ap.add_argument("--corridor", help="GeoJSON of the riparian corridor. Event mode only — "
                                       "masking would hide development in drift mode.")
    ap.add_argument("--basins", default="cullowhee_subbasins_incremental.geojson")
    ap.add_argument("--out", default="feed")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())
    if not a.month and not a.event:
        ap.error("--month YYYY-MM or --event BEFORE AFTER is required (or --selftest)")
    if a.event and a.month:
        ap.error("--month and --event are alternatives, not both")
    if a.corridor and not a.event:
        ap.error("--corridor is for --event only; masking to the channel would hide "
                 "development and harvest, which is what drift mode exists to see")

    import rasterio.transform
    from shapely.geometry import shape as shp
    import pyproj
    from shapely.ops import transform as shp_transform, unary_union

    gj = json.load(open(a.basins, encoding="utf-8"))
    to_utm = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{TARGET_EPSG}",
                                         always_xy=True).transform
    all_utm = unary_union([shp_transform(to_utm, shp(f["geometry"])).buffer(0)
                           for f in gj["features"]])
    minx, miny, maxx, maxy = all_utm.bounds
    minx, miny = minx - 200, miny - 200
    maxx, maxy = maxx + 200, maxy + 200
    w = int((maxx - minx) / PIXEL_M); h = int((maxy - miny) / PIXEL_M)
    transform = rasterio.transform.from_origin(minx, maxy, PIXEL_M, PIXEL_M)
    grid = ((h, w), transform)

    bidx, ids = build_basin_index(a.basins, (minx, miny, maxx, maxy), (h, w), transform)
    lons = [c[0] for f in gj["features"] for r in
            ([f["geometry"]["coordinates"][0]] if f["geometry"]["type"] == "Polygon"
             else [p[0] for p in f["geometry"]["coordinates"]]) for c in r]
    lats = [c[1] for f in gj["features"] for r in
            ([f["geometry"]["coordinates"][0]] if f["geometry"]["type"] == "Polygon"
             else [p[0] for p in f["geometry"]["coordinates"]]) for c in r]
    bbox = [min(lons), min(lats), max(lons), max(lats)]

    if a.event:
        base_ym, this_ym = a.event
        mode = "event"
        print(f"analysis grid {h}x{w} @ {PIXEL_M:.0f} m  ·  {len(ids)} sub-basins")
        print(f"EVENT mode: {this_ym} against {base_ym} (direct, not year-over-year)")
        if a.corridor:
            bidx = apply_corridor(bidx, a.corridor, (h, w), transform)
        else:
            print("no --corridor given: running basin-wide. The Helene control showed "
                  "riparian damage is ~1.6% of basin pixels and does not register.")
    else:
        base_ym, this_ym, mode = year_earlier(a.month), a.month, "drift"
        print(f"analysis grid {h}x{w} @ {PIXEL_M:.0f} m  ·  {len(ids)} sub-basins")
        print(f"comparing {this_ym} against {base_ym} (same month, one year earlier)")

    now, n_now, k_now = fetch_month(bbox, this_ym, grid)
    base, n_base, k_base = fetch_month(bbox, base_ym, grid)
    if now is None or base is None:
        sys.exit(f"no usable scenes: {this_ym}={k_now} items, {base_ym}={k_base} items")
    print(f"scenes used: {k_now} ({this_ym}), {k_base} ({base_ym})")

    os.makedirs(a.out, exist_ok=True)
    prev_path = os.path.join(a.out, f"landcover_patches_{prev_month(this_ym)}.json")
    previous = json.load(open(prev_path)) if os.path.exists(prev_path) else []
    if mode == "event":
        previous = []   # a one-off comparison has nothing to persist against
    if not previous:
        print(f"note: no {prev_month(this_ym)} patch file — nothing can confirm this run")

    patches = detect_patches(now, base, bidx, ids)
    confirmed, provisional = apply_persistence(patches, previous)
    zs_now = zonal_stats(now, bidx, len(ids))
    zs_base = zonal_stats(base, bidx, len(ids))

    rows = []
    for i, bid in enumerate(ids):
        cf = [p for p in confirmed if p["basin_id"] == bid]
        low = zs_now[i]["valid_frac"] < MIN_VALID_FRAC or zs_base[i]["valid_frac"] < MIN_VALID_FRAC
        rows.append({
            "month": this_ym, "baseline_month": base_ym, "basin_id": bid,
            "ndvi_median": zs_now[i]["ndvi_median"],
            "ndvi_median_baseline": zs_base[i]["ndvi_median"],
            "ndvi_delta": (None if None in (zs_now[i]["ndvi_median"], zs_base[i]["ndvi_median"])
                           else round(zs_now[i]["ndvi_median"] - zs_base[i]["ndvi_median"], 4)),
            "valid_frac": zs_now[i]["valid_frac"],
            "valid_frac_baseline": zs_base[i]["valid_frac"],
            "confirmed_patches": len(cf),
            "confirmed_area_ha": round(sum(p["area_ha"] for p in cf), 3),
            "provisional_patches": len([p for p in provisional if p["basin_id"] == bid]),
            "insufficient_clear_sky": low,
            "provenance": "DERIVED: Sentinel-2 L2A NDVI, same-month YoY, "
                          f"drop<={NDVI_DROP}, min {MIN_PATCH_HA} ha, "
                          f"{PERSISTENCE_N}-month persistence. NOT a model input.",
        })

    led = os.path.join(a.out, "landcover_watch.csv")
    new = not os.path.exists(led)
    with open(led, "a", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        if new:
            wr.writeheader()
        wr.writerows(rows)
    json.dump(confirmed + provisional,
              open(os.path.join(a.out, f"landcover_patches_{this_ym}.json"), "w"), indent=1)

    print(f"\n{'basin':<16}{'NDVI Δ':>9}{'clear':>8}{'patches':>9}{'total ha':>10}{'longest':>9}")
    print("-" * 62)
    for r in rows:
        d = f"{r['ndvi_delta']:+.3f}" if r["ndvi_delta"] is not None else "  --"
        flag = "  <- low clear-sky, treat as no observation" if r["insufficient_clear_sky"] else ""
        allp=[p for p in (confirmed+provisional) if p["basin_id"]==r["basin_id"]]
        area=sum(p["area_ha"] for p in allp)
        longest=max([p["long_axis_m"] for p in allp], default=0)
        print(f"{r['basin_id']:<16}{d:>9}{r['valid_frac']*100:>7.0f}%"
              f"{len(allp):>9}{area:>10.2f}{longest:>7} m{flag}")
    print(f"\nwrote {led} and the patch file. Nothing was written to basins.py — "
          "confirmed patches are input to the quarterly human review, not to the model.")


if __name__ == "__main__":
    main()
