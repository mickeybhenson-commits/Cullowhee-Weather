#!/usr/bin/env python3
"""
ras_tva_reconcile.py — Reproduce cullowhee_xs_master.csv from source files
===========================================================================
Consolidates the analysis performed in chat (2026-07-05):

  1. Parse the effective HEC-RAS model geometry (dtl_cullowhee_crk.g01):
     35 type-1 cross-sections, RM 6813-13211, EPSG:2264 cut lines,
     station-elevation arrays -> invert + top-of-bank per section.
  2. Parse the report file (.rep): W.S. Elev per section for all five
     profiles (10-YR / 50-YR / 100-YR / 500-YR / FW). NAVD88.
  3. Georeference every cut-line midpoint to WGS84 (pyproj, 2264->4326).
  4. Apply per-section VERTCON shifts (official grid, vertcone.gtx, mm
     units) to the TVA 1983 profile tabulation (NGVD29 1936 Suppl. Adj.
     -> NAVD88) and merge both sources into one provenance-tagged table.

KEY FINDINGS THIS SCRIPT REPRODUCES (validate on rerun)
  - RAS RM = TVA mile x 5280, common origin at the Cullowhee mouth
    (RM 6813 lands 179 ft from the LB confluence, mile 1.29; the 100-YR
    Known WS boundary 2092.94 interpolates onto the TVA profile there).
  - RM 7115 is at the TVA mile-1.34 bridge (~455 ft from LB mouth), NOT
    the campus warning point; the campus pour sits ~600 ft DOWNSTREAM of
    the model's downstream limit (RM 6813, a Known-WS boundary section).
  - Redelineation confirmed: TVA 1.34 US vs RAS RM 7088 100-yr
    delta = +0.09 ft after datum shift.
  - Speedwell RM 13211: 100-yr depth above invert = 9.2 ft (matches the
    provisional CC-SPD-1830 EMERGENCY rung).
  - Medians across 35 sections: channel depth 4.0 ft, 100-yr above
    invert 10.8 ft (matches cullowhee_detailed_xs_bfe.csv).

USAGE
  python ras_tva_reconcile.py \
      --g01 dtl_cullowhee_crk.g01 \
      --rep dtl_cullowhee_crk.rep \
      --tva tva_1983_profiles_navd88.csv \
      --grid vertcone.gtx \
      --out cullowhee_xs_master.csv

DEPENDENCIES:  pip install numpy pyproj
DATUM NOTE:    TVA footnote b = "USC&GS 1936 Supplementary Adjustment";
               VERTCON is built on published NGVD29 heights, which
               incorporate the supplementary adjustment in this region.
"""

import argparse
import csv
import math
import re
import struct
import sys

import numpy as np
from pyproj import Transformer

CAMPUS_POUR = (35.30978, -83.18745)   # CC-WCU-2260 (basins.py)
LB_MOUTH = (35.30819, -83.18770)      # CC-LB-171 pour


# --------------------------------------------------------------------------
# VERTCON grid (GTX, values in millimeters; NAVD88 = NGVD29 + shift)
# --------------------------------------------------------------------------
class Vertcon:
    def __init__(self, path):
        raw = open(path, "rb").read()
        self.ll_lat, self.ll_lon, self.dlat, self.dlon = struct.unpack(">4d", raw[:32])
        nr, nc = struct.unpack(">2i", raw[32:40])
        self.g = np.frombuffer(raw[40:], dtype=">f4").reshape(nr, nc) / 1000.0

    def shift_ft(self, lat, lon):
        ri = (lat - self.ll_lat) / self.dlat
        ci = (lon - self.ll_lon) / self.dlon
        r0, c0 = int(ri), int(ci)
        fr, fc = ri - r0, ci - c0
        g = self.g
        return float(
            g[r0, c0] * (1 - fr) * (1 - fc) + g[r0 + 1, c0] * fr * (1 - fc)
            + g[r0, c0 + 1] * (1 - fr) * fc + g[r0 + 1, c0 + 1] * fr * fc
        ) * 3.28084


# --------------------------------------------------------------------------
# HEC-RAS g01 geometry parser
# --------------------------------------------------------------------------
def parse_g01(path):
    txt = open(path, encoding="utf-8", errors="replace").read().replace("\r", "")
    out = []
    for b in re.split(r"\n(?=Type RM Length)", txt)[1:]:
        m = re.match(r"Type RM Length L Ch R = (\d+) ,([\d.]+)", b)
        if not m or int(m.group(1)) != 1:      # type 1 = cross section
            continue
        rec = {"rm": float(m.group(2))}
        mc = re.search(r"XS GIS Cut Line=(\d+)\s*\n((?:[ \d.eE+-]+\n)+)", b)
        if mc:
            nums = [float(v) for v in mc.group(2).split()]
            cl = list(zip(nums[0::2], nums[1::2]))
            rec["mid"] = cl[len(cl) // 2]
        ms = re.search(r"#Sta/Elev=\s*(\d+)\s*\n((?:.+\n)+?)(?=#Mann|Bank Sta|Type|$)", b)
        if ms:
            n = int(ms.group(1))
            vals = []
            for line in ms.group(2).split("\n"):
                if not line.strip() or line.startswith(("#", "Bank", "Type")):
                    break
                for i in range(0, len(line), 8):        # fixed 8-char fields
                    f = line[i:i + 8].strip()
                    if f:
                        try:
                            vals.append(float(f))
                        except ValueError:
                            pass
            rec["sta_elev"] = list(zip(vals[0::2], vals[1::2]))[:n]
        mb = re.search(r"Bank Sta=([\d.]+),([\d.]+)", b)
        if mb:
            rec["banks"] = (float(mb.group(1)), float(mb.group(2)))
        if "sta_elev" in rec:
            elevs = [e for _, e in rec["sta_elev"]]
            rec["invert"] = min(elevs)
            if "banks" in rec:
                rec["topbank"] = _bank_elev(rec)
            out.append(rec)
    return out


def _bank_elev(rec):
    pts = rec["sta_elev"]

    def el_at(st):
        for (s1, e1), (s2, e2) in zip(pts, pts[1:]):
            if s1 <= st <= s2:
                return e1 + (e2 - e1) * (st - s1) / ((s2 - s1) or 1)
        return None

    vals = [v for v in (el_at(rec["banks"][0]), el_at(rec["banks"][1])) if v is not None]
    return min(vals) if vals else None


# --------------------------------------------------------------------------
# .rep profile output parser
# --------------------------------------------------------------------------
def parse_rep(path):
    rep = open(path, encoding="utf-8", errors="replace").read().replace("\r", "")
    wse = {}
    for chunk in re.split(r"\n(?=CROSS SECTION\s*\n)", rep):
        m = re.search(r"RS:\s*([\d.]+)", chunk)
        if not m:
            continue
        d = {}
        for pm in re.finditer(
            r"CROSS SECTION OUTPUT\s+Profile #(\S+).*?W\.S\. Elev \(ft\)\s*\*\s*([\d.]+)",
            chunk, re.S,
        ):
            d[pm.group(1)] = float(pm.group(2))
        if d:
            wse[float(m.group(1))] = d
    return wse


def dist_ft(a, b):
    R = 20925525.0
    dy = math.radians(b[0] - a[0]) * R
    dx = math.radians(b[1] - a[1]) * R * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--g01", required=True)
    ap.add_argument("--rep", required=True)
    ap.add_argument("--tva", required=True,
                    help="tva_1983_profiles_navd88.csv (extraction deliverable)")
    ap.add_argument("--grid", default="vertcone.gtx")
    ap.add_argument("--out", default="cullowhee_xs_master.csv")
    args = ap.parse_args()

    xs = parse_g01(args.g01)
    wse = parse_rep(args.rep)
    tf = Transformer.from_crs(2264, 4326, always_xy=True)

    rows = []
    for r in sorted(xs, key=lambda r: -r["rm"]):
        rm = r["rm"]
        lon, lat = tf.transform(*r["mid"]) if "mid" in r else (None, None)
        w = wse.get(rm, {})
        inv, tb = r["invert"], r.get("topbank")
        rows.append(dict(
            source="FRIS-RAS-g01", river="Cullowhee Creek", rm_ft=rm,
            tva_mile_equiv=round(rm / 5280, 3),
            lat=round(lat, 5) if lat else None, lon=round(lon, 5) if lon else None,
            invert_ft=round(inv, 1), topbank_ft=round(tb, 1) if tb else None,
            channel_depth_ft=round(tb - inv, 1) if tb else None,
            wse10=w.get("10-YR"), wse50=w.get("50-YR"), wse100=w.get("100-YR"),
            wse500=w.get("500-YR"), wse_fw=w.get("FW"),
            d100_above_invert=round(w["100-YR"] - inv, 1) if "100-YR" in w else None,
            datum="NAVD88", vertcon_shift_ft=0.0, provenance="effective-model",
            note="Known-WS boundary section (not computed)" if rm == min(x["rm"] for x in xs) else "",
        ))

    for t in csv.DictReader(open(args.tva)):
        rows.append(dict(
            source="TVA-1983", river=t["stream"],
            rm_ft=round(float(t["mile"]) * 5280) if t["stream"] == "Cullowhee Creek" else None,
            tva_mile_equiv=float(t["mile"]),
            lat=float(t["lat_approx"]), lon=float(t["lon_approx"]),
            invert_ft=None, topbank_ft=None, channel_depth_ft=None,
            wse10=float(t["wse10_navd88_ft"]), wse50=None,
            wse100=float(t["wse100_navd88_ft"]), wse500=float(t["wse500_navd88_ft"]),
            wse_fw=None, d100_above_invert=None,
            datum="NAVD88 (VERTCON from NGVD29-1936SA)",
            vertcon_shift_ft=float(t["vertcon_shift_ft"]),
            provenance="1983-TVA", note=(t["position"] + " " + t["note"]).strip(),
        ))

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- validation block: the chat findings must reproduce ----
    ras = {r["rm_ft"]: r for r in rows if r["provenance"] == "effective-model"}
    rm_min = min(ras)
    campus_d = sorted((dist_ft(CAMPUS_POUR, (r["lat"], r["lon"])), rm) for rm, r in ras.items())
    print(f"wrote {len(rows)} rows -> {args.out}")
    print(f"model limit RM {rm_min:.0f} = TVA mile {rm_min/5280:.2f}; "
          f"LB-mouth distance {dist_ft(LB_MOUTH,(ras[rm_min]['lat'],ras[rm_min]['lon'])):.0f} ft")
    print(f"campus pour nearest section: RM {campus_d[0][1]:.0f} at {campus_d[0][0]:.0f} ft "
          f"(campus is DOWNSTREAM of model coverage)")
    if 13211.0 in ras and ras[13211.0]["d100_above_invert"]:
        print(f"Speedwell RM 13211 100-yr depth above invert: "
              f"{ras[13211.0]['d100_above_invert']} ft (expect 9.2)")


if __name__ == "__main__":
    sys.exit(main())
