#!/usr/bin/env python3
"""
bfe_to_thresholds.py
====================
Georeference the effective FEMA / HEC-RAS cross-sections and emit SURVEYED
WATCH / WARNING / EMERGENCY thresholds, per Cullowhee sub-basin, into a form
that drops straight into basins.py.

WHAT IT DOES
  1. Reads the HEC-RAS GIS export: the cross-section cut-line shapefile (one
     LineString per section, carrying the river station) and, optionally, the
     channel centerline.
  2. Reprojects everything to NC State Plane (ftUS) so distances are in feet.
  3. Snaps each sub-basin pour point (from cullowhee_roster.csv) to the nearest
     cut line.
  4. Joins the matched section to the already-extracted BFE table
     (cullowhee_detailed_xs_bfe.csv) and writes surveyed thresholds for every
     reach that lies ON the detailed-study mainstem.
  5. Reaches that fall OFF the studied mainstem (tributaries / headwaters,
     Zone A) are flagged "survey required" and left untouched for field work.

THRESHOLD MAPPING  (all depths above thalweg = invert; datum-free)
     WATCH      = bankfull depth             (roster bf_d_ft, Bieger regional)
     WARNING    = top-of-bank above thalweg  (FIS channel_depth_ft)      [surveyed]
     EMERGENCY  = 100-yr above thalweg       (FIS BFE_depth_above_invert) [surveyed]

CAMPUS PROTECTION
  CC-WCU-2260 is a bespoke 12-ft constructed channel; the FIS section there is
  the NATURAL channel (max depth ~8 ft, not the 12-ft wall). Its validated
  7 / 9 / 11 ft wall thresholds are PRESERVED and the FIS numbers are reported
  as a cross-check only.

INPUTS
  --xs      cross-section cut-line shapefile  (required to run for real)
  --cl      channel centerline shapefile      (optional)
  --bfe     cullowhee_detailed_xs_bfe.csv      (default: ./cullowhee_detailed_xs_bfe.csv)
  --roster  cullowhee_roster.csv               (default: ./cullowhee_roster.csv)
  --tol     on-profile distance tolerance, ft  (default: 150)

OUTPUTS
  basins_thresholds_surveyed.py    SURVEYED_THR dict, importable by basins.py
  basins_thresholds_surveyed.json  same, as JSON
  a printed report + the exact thr_ft / thr_src lines to paste into basins.py

DEPENDENCIES
  pip install pyshp shapely pyproj

DRY SELF-TEST  (no real shapefiles needed; fabricates mock cut lines through
the campus and Speedwell pour points and runs the full pipeline):
  python bfe_to_thresholds.py --selfcheck
"""

import argparse
import csv
import json
import os
import sys
import tempfile

import shapefile  # pyshp
from shapely.geometry import shape as shp_shape, Point
from shapely.ops import transform as shp_transform
from pyproj import CRS, Transformer

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------
TARGET_EPSG = 2264                       # NAD83 / North Carolina (ftUS)
DEFAULT_BFE = "cullowhee_detailed_xs_bfe.csv"
DEFAULT_ROSTER = "cullowhee_roster.csv"
DEFAULT_TOL_FT = 150.0                    # pour-point-to-cut-line; beyond = off-profile

# Reaches whose validated thresholds must NOT be overwritten by the FIS section.
# value = (WATCH, WARNING, EMERGENCY) ft, wall-relative, field-confirmed.
CONSTRUCTED = {"CC-WCU-2260": (7.0, 9.0, 11.0)}

# Sanity anchors from the handoff (river station, ft). A matched RM that lands
# far from these for a node we expect on-profile signals a CRS / field problem.
ANCHOR_RM = {"CC-WCU-2260": 7115, "CC-SPD-1830": 13211}
ANCHOR_TOL_FT = 1200.0

WGS84_WKT = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
             'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
             'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')

TF_WGS84 = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(TARGET_EPSG),
                                always_xy=True)


def info(msg): print(f"[info] {msg}")
def warn(msg): print(f"[warn] {msg}", file=sys.stderr)


# --------------------------------------------------------------------------
# CRS + shapefile helpers
# --------------------------------------------------------------------------
def crs_from_prj(shp_path):
    prj = os.path.splitext(shp_path)[0] + ".prj"
    if os.path.exists(prj):
        try:
            return CRS.from_wkt(open(prj).read())
        except Exception as e:
            warn(f"could not parse {prj} ({e}); assuming EPSG:4326")
    else:
        warn(f"no .prj beside {os.path.basename(shp_path)}; assuming EPSG:4326")
    return CRS.from_epsg(4326)


def reproject_to_target(geom, src_crs):
    tf = Transformer.from_crs(src_crs, CRS.from_epsg(TARGET_EPSG), always_xy=True)
    return shp_transform(tf.transform, geom)


def read_records(shp_path):
    r = shapefile.Reader(shp_path)
    fields = [f[0] for f in r.fields[1:]]            # drop DeletionFlag
    rows = [(sr.shape, dict(zip(fields, sr.record))) for sr in r.shapeRecords()]
    return fields, rows


def pt_2264(lat, lon):
    x, y = TF_WGS84.transform(lon, lat)
    return Point(x, y)


# --------------------------------------------------------------------------
# Station-field detection (name- and unit-agnostic, by value matching)
# --------------------------------------------------------------------------
def detect_station_field(fields, rows, csv_stations, tol=2.0):
    """Pick the cut-line attribute whose values best match the BFE stations.
    Tries the field as feet and as miles (x5280). Returns (field, scale, label)."""
    csvset = list(csv_stations)

    def hits(vals, scale):
        n = 0
        for v in vals:
            try:
                f = float(v) * scale
            except (TypeError, ValueError):
                continue
            if any(abs(f - s) <= tol for s in csvset):
                n += 1
        return n

    best = (None, 1.0, "feet", -1)
    for fld in fields:
        vals = [rec.get(fld) for _, rec in rows]
        for scale, label in ((1.0, "feet"), (5280.0, "miles")):
            n = hits(vals, scale)
            if n > best[3]:
                best = (fld, scale, label, n)
    return best


# --------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------
def load_xs(xs_shp, csv_stations):
    src = crs_from_prj(xs_shp)
    fields, rows = read_records(xs_shp)
    fld, scale, label, n = detect_station_field(fields, rows, csv_stations)
    if fld is None or n <= 0:
        raise SystemExit(f"[fatal] no station field in {xs_shp}; fields={fields}")
    info(f"cut-line station field = '{fld}' (interpreted as {label}); "
         f"{n}/{len(rows)} sections matched the BFE table")
    xs = []
    for shp, rec in rows:
        try:
            rm = float(rec[fld]) * scale
        except (TypeError, ValueError):
            continue
        geom = reproject_to_target(shp_shape(shp.__geo_interface__), src)
        if geom.geom_type == "MultiLineString":
            geom = max(geom.geoms, key=lambda g: g.length)
        xs.append({"rm": rm, "line": geom})
    xs.sort(key=lambda d: d["rm"])
    return xs


def load_pours(roster_csv):
    out = {}
    with open(roster_csv, newline="") as fh:
        for row in csv.DictReader(fh):
            bid = (row.get("basin_id") or "").strip()
            if not bid:
                continue
            try:
                lat, lon = float(row["pour_lat"]), float(row["pour_lon"])
            except (TypeError, ValueError, KeyError):
                continue
            try:
                bankfull = float(row.get("bf_d_ft"))
            except (TypeError, ValueError):
                bankfull = None
            out[bid] = {"lat": lat, "lon": lon, "pt": pt_2264(lat, lon),
                        "bankfull": bankfull, "role": (row.get("role") or "").strip()}
    return out


def load_bfe(bfe_csv):
    out = {}
    with open(bfe_csv, newline="") as fh:
        for row in csv.DictReader(fh):
            rm = int(round(float(row["river_sta_ft"])))
            rec = {}
            for k, v in row.items():
                try:
                    rec[k] = float(v)
                except (TypeError, ValueError):
                    rec[k] = v
            out[rm] = rec
    return out


def bfe_lookup(bfe, rm, tol=5.0):
    best, bd = None, 1e18
    for s in bfe:
        d = abs(s - rm)
        if d < bd:
            bd, best = d, s
    if best is not None and bd <= max(tol, 2.0):
        return bfe[best], best, bd
    return None, None, bd


# --------------------------------------------------------------------------
# Assignment
# --------------------------------------------------------------------------
def assign(pours, xs, bfe, tol_ft):
    results = {}
    for bid, p in pours.items():
        if not xs:
            results[bid] = {"status": "no-xs"}
            continue
        dist, x = min(((p["pt"].distance(x["line"]), x) for x in xs),
                      key=lambda t: t[0])
        rm = x["rm"]
        rec = {"dist_ft": round(dist, 1), "rm": round(rm, 1),
               "WATCH": p["bankfull"], "role": p["role"]}

        if dist > tol_ft:
            rec["status"] = "off-profile"
            results[bid] = rec
            continue

        row, matched, jd = bfe_lookup(bfe, rm)
        if not row:
            rec["status"] = "on-profile-no-bfe-row"
            results[bid] = rec
            continue

        rec.update(status="surveyed", matched_rm=matched,
                   WARNING=row.get("channel_depth_ft"),
                   EMERGENCY=row.get("BFE_depth_above_invert_ft"),
                   overbank_ft=row.get("BFE_above_topbank_ft"),
                   invert_el=row.get("invert_el_ft"))

        # campus and any other constructed reach: keep the wall thresholds
        if bid in CONSTRUCTED:
            w, wa, e = CONSTRUCTED[bid]
            rec["status"] = "constructed"
            rec["fis_xcheck"] = {"WARNING": rec["WARNING"], "EMERGENCY": rec["EMERGENCY"]}
            rec["WATCH"], rec["WARNING"], rec["EMERGENCY"] = w, wa, e

        # anchor sanity
        if bid in ANCHOR_RM and abs(matched - ANCHOR_RM[bid]) > ANCHOR_TOL_FT:
            warn(f"{bid}: matched RM {matched} is >{ANCHOR_TOL_FT:.0f} ft from "
                 f"expected anchor {ANCHOR_RM[bid]} — check CRS / station field")
        results[bid] = rec
    return results


# --------------------------------------------------------------------------
# Reporting + emit
# --------------------------------------------------------------------------
def _f(x, nd=1):
    return "—" if x is None else f"{x:.{nd}f}"


def report(results):
    order = ["status", "dist_ft", "rm", "WATCH", "WARNING", "EMERGENCY"]
    hdr = (f"{'basin':<14}{'status':<14}{'dist':>6}{'RM':>9}"
           f"{'WATCH':>7}{'WARN':>7}{'EMERG':>7}   note")
    print("\n" + hdr)
    print("-" * len(hdr))
    for bid in sorted(results):
        r = results[bid]
        note = ""
        if r["status"] == "constructed":
            xc = r.get("fis_xcheck", {})
            note = (f"wall thresholds kept; FIS natural-section x-check "
                    f"WARN {_f(xc.get('WARNING'))} / EMERG {_f(xc.get('EMERGENCY'))}")
        elif r["status"] == "surveyed":
            note = (f"FIS RM {r.get('matched_rm')}; 100-yr {_f(r.get('overbank_ft'))} ft "
                    f"over top-of-bank")
        elif r["status"] == "off-profile":
            note = "off detailed-study mainstem (Zone A) — survey / FIRM required"
        elif r["status"] == "on-profile-no-bfe-row":
            note = "near a cut line but no BFE row matched — check station join"
        elif r["status"] == "no-xs":
            note = "no cut lines loaded"
        print(f"{bid:<14}{r['status']:<14}{_f(r.get('dist_ft')):>6}{_f(r.get('rm')):>9}"
              f"{_f(r.get('WATCH')):>7}{_f(r.get('WARNING')):>7}{_f(r.get('EMERGENCY')):>7}   {note}")

    # ordering check
    print()
    for bid in sorted(results):
        r = results[bid]
        w, wa, e = r.get("WATCH"), r.get("WARNING"), r.get("EMERGENCY")
        if None not in (w, wa, e) and not (w < wa < e):
            warn(f"{bid}: thresholds not monotonic WATCH<WARNING<EMERGENCY "
                 f"({w} / {wa} / {e}) — verify")


def emit(results, outdir="."):
    surveyed = {}
    for bid, r in results.items():
        if r["status"] in ("surveyed", "constructed"):
            w, wa, e = r.get("WATCH"), r.get("WARNING"), r.get("EMERGENCY")
            if None in (w, wa, e):
                continue
            if r["status"] == "constructed":
                src = ("constructed 12-ft channel — wall thresholds (field+TVA); "
                       f"FEMA FIS RM {r.get('matched_rm')} natural-section is x-check only")
            else:
                src = (f"FEMA FIS / HEC-RAS g01 RM {r.get('matched_rm')} (NAVD88), "
                       "surveyed: WATCH=bankfull, WARNING=top-of-bank, EMERGENCY=100-yr")
            surveyed[bid] = {"WATCH": round(w, 1), "WARNING": round(wa, 1),
                             "EMERGENCY": round(e, 1), "src": src,
                             "rm": r.get("matched_rm"), "dist_ft": r.get("dist_ft")}

    pyp = os.path.join(outdir, "basins_thresholds_surveyed.py")
    with open(pyp, "w") as fh:
        fh.write('"""Surveyed thresholds from bfe_to_thresholds.py. '
                 'Import into basins.py and overlay where present."""\n\n')
        fh.write("SURVEYED_THR = " + json.dumps(surveyed, indent=4) + "\n")
    jsp = os.path.join(outdir, "basins_thresholds_surveyed.json")
    with open(jsp, "w") as fh:
        json.dump(surveyed, fh, indent=2)

    print("\n--- paste into basins.py (per matched reach) ---")
    for bid, t in sorted(surveyed.items()):
        print(f'# {bid}')
        print(f'thr_ft=({t["WATCH"]}, {t["WARNING"]}, {t["EMERGENCY"]}),')
        print(f'thr_src="{t["src"]}",')
    if not surveyed:
        print("(no on-profile reaches matched — nothing to write yet)")
    print(f"\nwrote {pyp}\nwrote {jsp}")
    return surveyed


# --------------------------------------------------------------------------
# Self-test: fabricate mock cut lines through the real pour points
# --------------------------------------------------------------------------
def _make_mock_xs(roster_csv, bfe_csv, path):
    """Build a mock cut-line shapefile: a section at every BFE station, located
    along a synthetic mainstem that passes through the campus (RM 7115) and
    Speedwell (RM 13211) pour points. Tributary pour points sit off it."""
    pours = load_pours(roster_csv)
    bfe = load_bfe(bfe_csv)
    a_bid, b_bid = "CC-WCU-2260", "CC-SPD-1830"
    (la, lo, ra) = pours[a_bid]["lat"], pours[a_bid]["lon"], ANCHOR_RM[a_bid]
    (lb, lob, rb) = pours[b_bid]["lat"], pours[b_bid]["lon"], ANCHOR_RM[b_bid]

    def loc(rm):
        t = (rm - ra) / (rb - ra)
        return (la + t * (lb - la), lo + t * (lob - lo))

    w = shapefile.Writer(path, shapeType=shapefile.POLYLINE)
    w.field("RiverStati", "N", size=12, decimal=2)
    for rm in sorted(bfe):
        lat, lon = loc(rm)
        w.line([[[lon - 0.0006, lat - 0.0002], [lon + 0.0006, lat + 0.0002]]])
        w.record(rm)
    w.close()
    open(path + ".prj", "w").write(WGS84_WKT)


def selfcheck(bfe_csv, roster_csv, tol_ft):
    info("SELF-TEST: fabricating mock cut lines through campus + Speedwell pour points")
    td = tempfile.mkdtemp()
    mock = os.path.join(td, "mock_xs")
    _make_mock_xs(roster_csv, bfe_csv, mock)
    bfe = load_bfe(bfe_csv)
    pours = load_pours(roster_csv)
    xs = load_xs(mock + ".shp", set(bfe.keys()))
    results = assign(pours, xs, bfe, tol_ft)
    report(results)
    emit(results, outdir=td)
    print(f"\n[selfcheck] artifacts in {td}")
    return results


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Emit surveyed thresholds from the FIS cross-sections.")
    ap.add_argument("--xs", help="cross-section cut-line shapefile (.shp)")
    ap.add_argument("--cl", help="channel centerline shapefile (.shp), optional")
    ap.add_argument("--bfe", default=DEFAULT_BFE)
    ap.add_argument("--roster", default=DEFAULT_ROSTER)
    ap.add_argument("--tol", type=float, default=DEFAULT_TOL_FT,
                    help="on-profile distance tolerance, ft")
    ap.add_argument("--out", default=".", help="output directory")
    ap.add_argument("--selfcheck", action="store_true",
                    help="run the dry self-test (no real shapefiles needed)")
    args = ap.parse_args()

    if args.selfcheck:
        selfcheck(args.bfe, args.roster, args.tol)
        return
    if not args.xs:
        ap.error("--xs is required (or use --selfcheck)")

    bfe = load_bfe(args.bfe)
    pours = load_pours(args.roster)
    xs = load_xs(args.xs, set(bfe.keys()))
    if args.cl:
        info(f"centerline supplied ({os.path.basename(args.cl)}); cut-line distance "
             "is used for assignment regardless")
    results = assign(pours, xs, bfe, args.tol)
    report(results)
    emit(results, outdir=args.out)


if __name__ == "__main__":
    main()
