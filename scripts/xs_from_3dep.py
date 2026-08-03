#!/usr/bin/env python3
"""
xs_from_3dep.py — surveyed cross-sections for the UNSTUDIED reaches, with no
DEM download.
=============================================================================
Companion to lidar_xsection_cutter.py. Same bank-detection logic, same output
shape — but instead of reading local NC QL2 GeoTIFF tiles it samples the USGS
3DEP ImageServer directly over HTTPS. NC QL2 is the source layer inside 3DEP at
1 m, so the elevations are the same data without the multi-gigabyte download.

WHY THIS EXISTS
  data/cullowhee_xs_master.csv has 91 surveyed sections covering 2.49 mi, all
  in the lower watershed. Measured 2026-08-03, distance from each pour point to
  its nearest section:

      Distance to the nearest section was a misleading measure. What matters
      is whether surveyed CHANNEL GEOMETRY exists on the basin's OWN
      watercourse. Only 35 of the 91 rows carry invert / top-of-bank /
      channel-depth, and all 35 are FRIS-RAS sections on the Cullowhee Creek
      MAINSTEM. The other 56 are TVA-1983 water-surface elevations with no
      geometry at all.

      WCU Campus       yes  mainstem geometry 600 ft off, plus the field-
                            validated 11 ft = water in road
      Cox Branch       NO   the 68 ft section is the MAINSTEM at the
                            confluence: an 8.3 ft mainstem channel, not the
                            0.97 mi2 branch
      Long Branch      NO   13 sections exist, all TVA-1983 WSE-only
      Speedwell        NO   nearest geometry 2 940 ft downstream, mainstem
      Tilley Creek     NO   zero sections of any kind
      Mtn. Lower       NO   nearest geometry 4 271 ft downstream, mainstem
      Upper Cullowhee  NO   nearest anything is the Tuckasegee, 3.9 mi off

  18.3 of the 22.6 mi2 draining to campus — 81% — sits above the nearest
  surveyed section, on thr_ft values that are bankfull x (1.0, 1.5, 2.0)
  arithmetic. Those four reaches protect the upper valley. This closes them.

THRESHOLDS PRODUCED  (depths above thalweg — datum-free, so no NAVD88 tie is
needed and the unverified FIMAN gage datum is irrelevant here)

  WATCH      bankfull depth from the section itself (lower detected bank)
  WARNING    top-of-bank above thalweg (higher detected bank) — out of bank
  EMERGENCY  depth at the 100-yr regression discharge, routed through the REAL
             section geometry by conveyance-weighted Manning rather than the
             rectangular idealisation in basins.py

  The third one is the quiet upgrade: basins.py currently rates these reaches
  as a rectangle (CC-SPD-1830: w=55.7 ft flat). A real section changes the
  stage produced by every discharge, not just the thresholds.

USAGE
  pip install numpy requests
  python3 xs_from_3dep.py --selfcheck                 # no network, verifies math
  python3 xs_from_3dep.py --basin CC-SPD-1830 --spacing 300 --width 250
  python3 xs_from_3dep.py --all --out-dir xs_out

  --spacing  ft between sections along the flowline (default 300)
  --width    total section width, ft (default 250; widen for the wider reaches)
  --npts     samples per section (default 201 -> ~1.25 ft at 250 ft width)

OUTPUT
  <out>/<basin>/<section_id>.csv     station_ft, elev_ft, lat, lon
  <out>/summary.csv                  thalweg, banks, bankfull depth, width
  <out>/thresholds_lidar.py          SURVEYED_THR dict, importable by basins.py
  printed report + the exact thr_ft / thr_src lines to paste

NETWORK
  USGS 3DEP ImageServer  elevation.nationalmap.gov  (no key)
  USGS NLDI              labs.waterdata.usgs.gov    (no key, --auto-centerline)
Both are public. Neither is reachable from a sandboxed environment; run this on
a workstation.
"""

import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

try:
    import numpy as np
except ImportError:
    sys.exit("needs numpy:  pip install numpy")

# --------------------------------------------------------------------------
THREEDEP = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
            "3DEPElevation/ImageServer/getSamples")
# NLDI base URLs, tried in order. api.water.usgs.gov is current (it is what
# feeds.py resolve_reach already uses); labs.waterdata.usgs.gov is the old host
# and now 404s. Listed rather than hard-coded so a future move is one line.
NLDI_BASES = [
    "https://api.water.usgs.gov/nldi/linked-data",            # current
    "https://labs.waterdata.usgs.gov/api/nldi/linked-data",   # legacy
    "https://labs-beta.waterdata.usgs.gov/api/nldi/linked-data",
]
CHUNK = 250          # points per 3DEP request
PAUSE = 0.4          # seconds between requests, be polite
FT_PER_M = 3.280839895

# Pour points, from basins.py. Sections are cut upstream from each.
POUR = {
    "CC-UP-503":     (35.23320, -83.18689),
    "CC-MS-1100":    (35.28203, -83.18599),
    "CC-TIL-705":    (35.28273, -83.18702),
    "CC-SPD-1830":   (35.28534, -83.18393),
    "CC-COX-097":    (35.30180, -83.18324),
    "CC-LB-171":     (35.30819, -83.18770),
    "CC-WCU-2260":   (35.30978, -83.18745),
    "CC-MOUTH-2340": (35.31709, -83.18037),
}

# Reaches with NO surveyed channel geometry on their own watercourse.
# Corrected 2026-08-03: an earlier pass measured distance to the nearest
# section and wrongly counted Cox Branch and Long Branch as covered.
UNSURVEYED = ["CC-SPD-1830", "CC-TIL-705", "CC-MS-1100", "CC-UP-503",
              "CC-COX-097", "CC-LB-171"]

# 100-yr regression discharge (cfs) and channel slope, from basins.py.
REG_Q100 = {"CC-UP-503": 1500, "CC-MS-1100": 2740, "CC-TIL-705": 1950,
            "CC-SPD-1830": 4050, "CC-COX-097": 426, "CC-LB-171": 658,
            "CC-WCU-2260": 4760, "CC-MOUTH-2340": 4880}
SLOPE = {"CC-UP-503": 0.0888, "CC-MS-1100": 0.0446, "CC-TIL-705": 0.0547,
         "CC-SPD-1830": 0.0425, "CC-COX-097": 0.1000, "CC-LB-171": 0.0753,
         "CC-WCU-2260": 0.0050, "CC-MOUTH-2340": 0.0050}
NVAL = {"CC-UP-503": 0.045, "CC-MS-1100": 0.045, "CC-TIL-705": 0.050,
        "CC-SPD-1830": 0.045, "CC-COX-097": 0.045, "CC-LB-171": 0.045,
        "CC-WCU-2260": 0.035, "CC-MOUTH-2340": 0.045}


# --------------------------------------------------------------------------
# geodesy — local flat-earth is fine over a 23 mi2 watershed
# --------------------------------------------------------------------------
def ll_to_ft(lat, lon, lat0, lon0):
    """Local ENU feet from a reference lat/lon."""
    k = 364000.0                       # ft per degree latitude, ~constant
    return ((lon - lon0) * k * math.cos(math.radians(lat0)),
            (lat - lat0) * k)


def ft_to_ll(x, y, lat0, lon0):
    k = 364000.0
    return (lat0 + y / k, lon0 + x / (k * math.cos(math.radians(lat0))))


def haversine_ft(a, b):
    R = 20902231.0                     # earth radius, ft
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


# --------------------------------------------------------------------------
# centerline
# --------------------------------------------------------------------------
def _try_json(url, timeout=90):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def nldi_upstream(lat, lon, km=25, verbose=True):
    """NHD flowlines upstream of a point, as [[(lat,lon),...], ...].

    Walks NLDI_BASES until one answers. Raises with every attempt listed if
    none do, so a host move shows up as a clear message and not a bare 404.
    """
    q = urllib.parse.urlencode({"coords": f"POINT({lon} {lat})"})
    comid, base, tried = None, None, []
    for b in NLDI_BASES:
        try:
            j = _try_json(f"{b}/comid/position?{q}", timeout=60)
            feats = j.get("features") or []
            if not feats:
                tried.append(f"{b}  -> 200 but no feature at this point")
                continue
            comid = int(feats[0]["properties"]["comid"])
            base = b
            if verbose:
                print(f"  NLDI {b}  comid {comid}")
            break
        except Exception as e:
            tried.append(f"{b}  -> {e}")
    if comid is None:
        raise RuntimeError("no NLDI host answered:\n    " + "\n    ".join(tried))

    q2 = urllib.parse.urlencode({"distance": km})
    url = f"{base}/comid/{comid}/navigation/UT/flowlines?{q2}"
    fc = _try_json(url, timeout=180)
    out = []
    for f in fc.get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") == "LineString":
            out.append([(c[1], c[0]) for c in g["coordinates"]])
        elif g.get("type") == "MultiLineString":
            for part in g["coordinates"]:
                out.append([(c[1], c[0]) for c in part])
    return out


def read_centerline_csv(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    return [[(float(r["lat"]), float(r["lon"])) for r in rows]]


# --------------------------------------------------------------------------
# perpendicular cuts
# --------------------------------------------------------------------------
def cut_lines(line, spacing_ft, width_ft, lat0, lon0):
    """Perpendicular cut lines every spacing_ft along a polyline."""
    pts = [ll_to_ft(la, lo, lat0, lon0) for la, lo in line]
    if len(pts) < 2:
        return []
    cum, acc = [0.0], 0.0
    for i in range(1, len(pts)):
        acc += math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        cum.append(acc)
    total = cum[-1]
    if total < spacing_ft:
        return []
    out, s = [], spacing_ft / 2.0
    while s < total:
        j = max(1, min(len(cum) - 1, next(i for i in range(1, len(cum)) if cum[i] >= s)))
        f = (s - cum[j - 1]) / max(cum[j] - cum[j - 1], 1e-9)
        cx = pts[j - 1][0] + f * (pts[j][0] - pts[j - 1][0])
        cy = pts[j - 1][1] + f * (pts[j][1] - pts[j - 1][1])
        dx, dy = pts[j][0] - pts[j - 1][0], pts[j][1] - pts[j - 1][1]
        m = math.hypot(dx, dy) or 1.0
        px, py = -dy / m, dx / m                       # unit perpendicular
        h = width_ft / 2.0
        out.append((ft_to_ll(cx - px * h, cy - py * h, lat0, lon0),
                    ft_to_ll(cx + px * h, cy + py * h, lat0, lon0),
                    ft_to_ll(cx, cy, lat0, lon0), s))
        s += spacing_ft
    return out


# --------------------------------------------------------------------------
# 3DEP sampling
# --------------------------------------------------------------------------
def sample_3dep(points):
    """points: [(lat,lon)] -> [elev_ft or None], chunked."""
    out = []
    for i in range(0, len(points), CHUNK):
        chunk = points[i:i + CHUNK]
        geom = {"points": [[lo, la] for la, lo in chunk],
                "spatialReference": {"wkid": 4326}}
        body = urllib.parse.urlencode({
            "geometry": json.dumps(geom),
            "geometryType": "esriGeometryMultipoint",
            "returnFirstValueOnly": "true",
            "interpolation": "RSP_BilinearInterpolation",
            "f": "json"}).encode()
        req = urllib.request.Request(THREEDEP, data=body,
                                     headers={"Content-Type":
                                              "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                j = json.load(r)
        except Exception as e:
            print(f"    3DEP request failed ({e}); {len(chunk)} pts -> None",
                  file=sys.stderr)
            out += [None] * len(chunk)
            continue
        vals = {int(s["locationId"]): s.get("value") for s in j.get("samples", [])}
        for k in range(len(chunk)):
            v = vals.get(k)
            try:
                out.append(float(v) * FT_PER_M if v not in (None, "", "NoData") else None)
            except (TypeError, ValueError):
                out.append(None)
        time.sleep(PAUSE)
    return out


# --------------------------------------------------------------------------
# geometry analysis — same approach as lidar_xsection_cutter.detect_banks
# --------------------------------------------------------------------------
def _smooth(e, w=5):
    k = np.ones(w) / w
    return np.convolve(e, k, mode="same")


def detect_banks(sta, elev, search_ft=100.0, edge_pad=6):
    i0 = int(np.nanargmin(elev))
    res = {"thalweg_sta": float(sta[i0]), "thalweg_elev": float(elev[i0])}
    k_full = np.gradient(np.gradient(_smooth(elev)))
    for side in ("left", "right"):
        idx = (np.arange(edge_pad, i0) if side == "left"
               else np.arange(i0 + 1, len(sta) - edge_pad))
        idx = idx[np.abs(sta[idx] - sta[i0]) <= search_ft] if len(idx) else idx
        if len(idx) < 3:
            res[f"{side}_bank_sta"] = float("nan")
            res[f"{side}_bank_elev"] = float("nan")
            continue
        j = idx[int(np.nanargmin(k_full[idx]))]
        res[f"{side}_bank_sta"] = float(sta[j])
        res[f"{side}_bank_elev"] = float(elev[j])
    lb, rb = res["left_bank_elev"], res["right_bank_elev"]
    res["bankfull_depth_ft"] = float(np.nanmin([lb, rb]) - res["thalweg_elev"])
    res["topbank_depth_ft"] = float(np.nanmax([lb, rb]) - res["thalweg_elev"])
    return res


def conveyance_q(sta, elev, wse, n, slope):
    """Manning discharge for a water-surface elevation over a real section,
    summed across wetted sub-panels (conveyance method)."""
    A = P = 0.0
    for i in range(len(sta) - 1):
        d1, d2 = wse - elev[i], wse - elev[i + 1]
        if d1 <= 0 and d2 <= 0:
            continue
        dx = sta[i + 1] - sta[i]
        if d1 > 0 and d2 > 0:
            A += 0.5 * (d1 + d2) * dx
            P += math.hypot(dx, elev[i + 1] - elev[i])
        else:                                   # partial panel at the edge
            dd = d1 if d1 > 0 else d2
            frac = dd / (abs(d1 - d2) or 1e-9)
            A += 0.5 * dd * dx * frac
            P += math.hypot(dx * frac, dd)
    if A <= 0 or P <= 0:
        return 0.0
    R = A / P
    return (1.49 / n) * A * R ** (2.0 / 3.0) * math.sqrt(slope)


def depth_for_q(sta, elev, q_target, n, slope):
    """Depth above thalweg that conveys q_target through the real section."""
    thal = float(np.nanmin(elev))
    lo, hi = 0.0, float(np.nanmax(elev) - thal) + 5.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if conveyance_q(sta, elev, thal + mid, n, slope) < q_target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------
def selfcheck():
    """No network. Synthetic trapezoidal valley: does the math recover it?"""
    print("SELF-CHECK — synthetic section, known geometry\n")
    sta = np.linspace(-125, 125, 201)
    # 30 ft bottom, 1:3 banks to 6 ft, then wide floodplain at 6.5 ft
    elev = np.where(np.abs(sta) <= 15, 100.0,
           np.where(np.abs(sta) <= 33, 100.0 + (np.abs(sta) - 15) / 3.0,
                    106.5))
    b = detect_banks(sta, elev)
    print(f"  thalweg elev   {b['thalweg_elev']:.2f}   (true 100.00)")
    print(f"  bankfull depth {b['bankfull_depth_ft']:.2f} ft (true ~6.0)")
    print(f"  top-of-bank    {b['topbank_depth_ft']:.2f} ft")
    for q in (500, 2000, 4050):
        d = depth_for_q(sta, elev, q, 0.045, 0.0425)
        back = conveyance_q(sta, elev, 100.0 + d, 0.045, 0.0425)
        print(f"  Q={q:>5} cfs -> depth {d:5.2f} ft   (round-trip {back:6.0f} cfs)")
    ok = abs(b["thalweg_elev"] - 100.0) < 0.01 and 5.0 < b["bankfull_depth_ft"] < 7.0
    print("\n  " + ("PASS" if ok else "FAIL — geometry not recovered"))
    return 0 if ok else 1


def run(basins, spacing, width, npts, out_dir, centerline_csv, nav_km):
    os.makedirs(out_dir, exist_ok=True)
    lat0, lon0 = 35.27, -83.19
    summary, thresholds = [], {}

    for bid in basins:
        print(f"\n=== {bid} ===")
        try:
            lines = (read_centerline_csv(centerline_csv) if centerline_csv
                     else nldi_upstream(*POUR[bid], km=nav_km))
        except Exception as e:
            print(f"  centerline unavailable: {e}", file=sys.stderr)
            continue
        print(f"  {len(lines)} flowline(s) from NLDI")

        cuts = []
        for ln in lines:
            cuts += cut_lines(ln, spacing, width, lat0, lon0)
        print(f"  {len(cuts)} cut lines at {spacing} ft spacing, {width} ft wide")
        if not cuts:
            continue

        bdir = os.path.join(out_dir, bid)
        os.makedirs(bdir, exist_ok=True)
        good = []
        for k, (p1, p2, mid, s) in enumerate(cuts):
            pts = [(p1[0] + (p2[0] - p1[0]) * t / (npts - 1),
                    p1[1] + (p2[1] - p1[1]) * t / (npts - 1)) for t in range(npts)]
            elev = sample_3dep(pts)
            if sum(e is not None for e in elev) < npts * 0.8:
                print(f"    section {k:03d}: too many gaps, skipped")
                continue
            e = np.array([np.nan if v is None else v for v in elev], float)
            e = np.array([v if not np.isnan(v) else np.nanmean(e) for v in e])
            sta = np.linspace(-width / 2.0, width / 2.0, npts)
            b = detect_banks(sta, e)
            sid = f"{bid}_{k:03d}"
            with open(os.path.join(bdir, sid + ".csv"), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["station_ft", "elev_ft", "lat", "lon"])
                for i in range(npts):
                    w.writerow([round(sta[i], 2), round(e[i], 2),
                                round(pts[i][0], 7), round(pts[i][1], 7)])
            d100 = depth_for_q(sta, e, REG_Q100[bid], NVAL[bid], SLOPE[bid])
            rec = dict(section_id=sid, basin_id=bid, lat=mid[0], lon=mid[1],
                       station_along_ft=round(s, 1),
                       thalweg_elev_ft=round(b["thalweg_elev"], 2),
                       bankfull_depth_ft=round(b["bankfull_depth_ft"], 2),
                       topbank_depth_ft=round(b["topbank_depth_ft"], 2),
                       d100_above_thalweg_ft=round(d100, 2))
            summary.append(rec)
            good.append(rec)
            print(f"    {sid}  thalweg {rec['thalweg_elev_ft']:.1f} ft  "
                  f"bankfull {rec['bankfull_depth_ft']:.2f}  "
                  f"topbank {rec['topbank_depth_ft']:.2f}  "
                  f"d100 {rec['d100_above_thalweg_ft']:.2f}")

        if good:
            # controlling section = the one that goes out of bank soonest
            ctl = min(good, key=lambda r: r["topbank_depth_ft"])
            thresholds[bid] = (round(ctl["bankfull_depth_ft"], 2),
                               round(ctl["topbank_depth_ft"], 2),
                               round(ctl["d100_above_thalweg_ft"], 2),
                               ctl["section_id"], len(good))

    if summary:
        with open(os.path.join(out_dir, "summary.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(summary[0]))
            w.writeheader()
            w.writerows(summary)

    if thresholds:
        p = os.path.join(out_dir, "thresholds_lidar.py")
        with open(p, "w") as f:
            f.write('"""GENERATED by xs_from_3dep.py — depths above thalweg, '
                    'datum-free.\nWATCH=bankfull  WARNING=top-of-bank  '
                    'EMERGENCY=100-yr through the real section.\n"""\n'
                    "SURVEYED_THR = {\n")
            for bid, (a, b_, c, sid, n) in thresholds.items():
                f.write(f'    "{bid}": ({a}, {b_}, {c}),'
                        f'  # controlling section {sid}, of {n}\n')
            f.write("}\n")
        print(f"\nwrote {p}")
        print("\nPaste into basins.py:\n")
        for bid, (a, b_, c, sid, n) in thresholds.items():
            print(f'  {bid}:')
            print(f'    thr_ft=({a}, {b_}, {c}),')
            print(f'    thr_src="SURVEYED: NC QL2 via 3DEP; controlling section '
                  f'{sid} of {n}; WATCH=bankfull, WARNING=top-of-bank, '
                  f'EMERGENCY=100-yr reg_q through the real section",')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--probe-nldi", action="store_true",
                    help="test NLDI reachability at every pour point, then exit")
    ap.add_argument("--basin", action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--spacing", type=float, default=300.0)
    ap.add_argument("--width", type=float, default=250.0)
    ap.add_argument("--npts", type=int, default=201)
    ap.add_argument("--nav-km", type=float, default=25.0)
    ap.add_argument("--centerline")
    ap.add_argument("--out-dir", default="xs_out")
    a = ap.parse_args()
    if a.selfcheck:
        return selfcheck()
    if a.probe_nldi:
        print("NLDI probe — which host answers, and how much network is upstream?\n")
        bad = 0
        for bid, (la, lo) in POUR.items():
            try:
                lines = nldi_upstream(la, lo, km=a.nav_km)
                n = sum(len(x) for x in lines)
                print(f"  {bid:<15} {len(lines):>4} flowlines, {n:>5} vertices")
            except Exception as e:
                bad += 1
                print(f"  {bid:<15} FAILED: {e}")
        return 1 if bad else 0
    basins = a.basin or (list(POUR) if a.all else UNSURVEYED)
    return run(basins, a.spacing, a.width, a.npts, a.out_dir,
               a.centerline, a.nav_km)


if __name__ == "__main__":
    sys.exit(main())
