#!/usr/bin/env python3
"""
helene_mrms_reconstruct.py — what rain actually fell on each sub-basin during Helene.

WHY THIS EXISTS
---------------
The Helene calibration anchor is not a point. It is a ridge. Holding the surveyed
campus peak fixed at 2,274 cfs, these all reproduce it exactly (deployed cwm_model,
K24A hourly shape rescaled to each total):

      basin rainfall        antecedent wetness w
         7.48 in                   1.000        <- hard floor: saturated soil
         8.00 in                   0.774
         9.00 in                   0.454
        10.00 in                   0.248        <- the repo's anchor
        11.00 in                   0.084

THE RIDGE IS AT LEAST TWO-DIMENSIONAL, SO A TOTAL ALONE WILL NOT COLLAPSE IT.
Hold the total at 10.00 in and vary only the hourly pattern:

      K24A observed shape      w = 0.248
      back-loaded ramp         w = 0.434
      uniform over 48 h        OFF-RIDGE — saturated soil cannot reach 2,274 cfs

That is why this script writes an HOURLY series per basin rather than a total, and why
the verdict is computed by helene_solve_wetness.py from the measured shape. Any reading
of the event total against the table above is PRELIMINARY.

`basins.py` HELENE_2024 records "7.0-8.4 in / 36 h (COOP-anchored), ARC-III (P5 2.49\\")"
— the wet end. `noah_permeability_lever_is_wetness_2026-08-10.md` records 10 in on
"drought-dry" soil — the dry end. Both reproduce the surveyed peak. They are the SAME
solution and have been cited as if they corroborated one another.

The 10-inch figure was never measured over the basins. K24A, a valley airport gauge,
recorded 7.22 in. The difference decides whether Helene was a dry-antecedent event (the
current headline, and the basis for "the defining flood in this watershed's record is an
antecedent-moisture story") or a wet-antecedent one — which would invert that reading.

Note where 7.22 sits: BELOW the 7.48 in floor. On the valley gauge's own rainfall, no
antecedent condition whatsoever reproduces the surveyed peak. So the basins must have
received more rain than the valley did — orographic enhancement — and the question is
only how much. That is a real constraint on the answer before any radar is read.

UPDATE 2026-08-12 — READ THIS BEFORE INTERPRETING THE OUTPUT
------------------------------------------------------------
The ridge is now understood, and it is NOT primarily a rainfall-measurement problem.
See noah_helene_ridge_is_frozen_wetness_2026-08-12.md. The measured USCRN trajectory
shows the catchment going from w ~0.5 to saturated DURING the event, before the main
rainfall day. The model carries ONE frozen w per event, so to fit the surveyed peak it
must trade rainfall against wetness — which is exactly the ridge. The 10-inch figure was
compensating for a frozen state variable, not describing rainfall.

So this script no longer settles the wet-vs-dry question. It still answers two things
nothing else can, and both are worth the run:

  1. LOCKSTEP. Did the eight basins actually receive different rainfall? This produces
     eight independent basin means for the defining event. It decides whether
     basin-averaged QPE is worth building for the live path.
  2. THE OROGRAPHIC GAP. K24A's 7.22 in sits BELOW the 7.48 in floor the static model
     needs even at full saturation, so the basins must have out-rained the valley. This
     measures by how much.

MRMS is the instrument for both. The Iowa State mtarchive holds
MultiSensor_QPE_01H_Pass2 back to October 2014, so the defining event is inside the
archive, and the basin masks already exist.

SECOND QUESTION, FOR FREE
-------------------------
The live path forces all eight basins from Open-Meteo point queries at basin centroids,
which likely resolve to one or two model grid cells — the suspected cause of the basins
moving in lockstep. This produces eight genuinely independent basin means for the
defining event. If the measured spread is also near zero, lockstep is physics. If it is
large, it is an artifact of the forcing.

PRODUCT CHOICE
--------------
Pass2 (gauge-corrected, ~1 h latency) is right here and wrong for warning. This is a
reconstruction of a 2024 event, so latency is irrelevant and quality is everything.
mrms_live.py makes the opposite trade for the opposite reason.

CAVEAT THAT MUST TRAVEL WITH THE ANSWER
---------------------------------------
Radar underestimated Helene's totals by 20-33% against gauges in this terrain (Godfrey,
UNC Collaboratory), and 30-50% of mid/high-elevation accumulation comes from
seeder-feeder rain radar systematically misses (Duke/UNCA). Pass2 is gauge-corrected,
which mitigates but does not remove this. Treat the result as a well-constrained LOWER
BOUND, and read the sensitivity table this script prints before concluding anything.

    python helene_mrms_reconstruct.py                    # full event window
    python helene_mrms_reconstruct.py --start 2024-09-26T00 --end 2024-09-27T12
    python helene_mrms_reconstruct.py --csv helene_basin_rain.csv

Deps: eccodes (pip install eccodes). Network access to mtarchive.geol.iastate.edu.
"""

import argparse
import csv
import datetime as dt
import gzip
import json
import os
import sys
import urllib.error
import urllib.request

# --------------------------------------------------------------------------- #
_HERE = os.path.dirname(os.path.abspath(__file__))
MASKS = os.path.join(_HERE, "ledger", "mrms_masks.json")
if not os.path.exists(MASKS):
    MASKS = os.path.join(_HERE, "mrms_masks.json")

URL = ("https://mtarchive.geol.iastate.edu/{y:04d}/{m:02d}/{d:02d}/mrms/ncep/"
       "MultiSensor_QPE_01H_Pass2/"
       "MultiSensor_QPE_01H_Pass2_00.00_{y:04d}{m:02d}{d:02d}-{h:02d}0000.grib2.gz")

# Helene's Cullowhee window. Starts before the main surge to catch the predecessor
# rain event of 25-26 Sep, which is what set the antecedent condition and is exactly
# the thing in dispute.
DEFAULT_START = dt.datetime(2024, 9, 25, 0, tzinfo=dt.timezone.utc)
DEFAULT_END = dt.datetime(2024, 9, 28, 0, tzinfo=dt.timezone.utc)

MIN_VALID = 0.5
BIG = 1.0e10
SURVEYED_PEAK_CFS = 2274.0        # campus, NCGS marks via FRIS profile
K24A_TOTAL_IN = 7.22              # valley airport gauge, for comparison

# The ridge, solved against the DEPLOYED cwm_model.assess_event() with the K24A hourly
# shape rescaled to each total. Every row reproduces 2,274 cfs to within 1 cfs; that was
# verified forward, not assumed from the inverse solve. Regenerate these if the model,
# the calibration, or the surveyed peak changes -- they are not independent constants.
RIDGE = [
    (7.48, 1.000, "ARC-III / saturated"),
    (7.93, 0.800, "ARC-III edge, basins.py"),
    (9.00, 0.454, "neither record"),
    (9.72, 0.300, "dry edge, perm. note"),
    (10.00, 0.248, "the repo's anchor"),
    (10.57, 0.150, "drought-dry, perm. note"),
]
FLOOR_IN = RIDGE[0][0]            # below this, no antecedent condition suffices
WET_MAX_IN = RIDGE[1][0]          # <= this -> inside basins.py's ARC-III claim
DRY_MIN_IN = RIDGE[3][0]          # >= this -> inside the permeability note's claim


def ridge_w(total_in):
    """Wetness implied by a rainfall total, by interpolation on RIDGE. Display only.

    Returns a string, deliberately: outside the bracket there is no interpolable
    answer and the caller must not receive a number that looks computed.
    """
    if total_in < FLOOR_IN:
        return "OFF-RIDGE (none)"
    if total_in >= RIDGE[-1][0]:
        return f"<{RIDGE[-1][1]:.3f}"
    for (x0, y0, _), (x1, y1, _) in zip(RIDGE, RIDGE[1:]):
        if x0 <= total_in <= x1:
            return f"~{y0 + (total_in - x0) / (x1 - x0) * (y1 - y0):.3f}"
    return "?"

try:
    import eccodes
    _DECODER_OK = True
except Exception:                                          # noqa: BLE001
    _DECODER_OK = False


def load_masks():
    with open(MASKS) as f:
        return json.load(f)["basins"]


def _wanted(basins):
    return {(c["lat"], c["lon_e"]) for b in basins.values() for c in b["cells"]}


def grid_values(gz, basins):
    gid = eccodes.codes_new_from_message(gzip.decompress(gz))
    try:
        ni = eccodes.codes_get(gid, "Ni"); nj = eccodes.codes_get(gid, "Nj")
        lat1 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
        lon1 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
        di = eccodes.codes_get(gid, "iDirectionIncrementInDegrees")
        dj = eccodes.codes_get(gid, "jDirectionIncrementInDegrees")
        jpos = eccodes.codes_get(gid, "jScansPositively")
        ipos = eccodes.codes_get(gid, "iScansNegatively") == 0
        values = eccodes.codes_get_values(gid)
    finally:
        eccodes.codes_release(gid)
    if len(values) != ni * nj:
        raise RuntimeError(f"grid size mismatch {len(values)} != {ni}*{nj}")
    out = {}
    for lat, lon_e in _wanted(basins):
        col = (lon_e - lon1) / di if ipos else (lon1 - lon_e) / di
        row = (lat - lat1) / dj if jpos else (lat1 - lat) / dj
        ic, ir = round(col), round(row)
        if abs(col - ic) > 0.25 or abs(row - ir) > 0.25:
            raise RuntimeError(f"cell ({lat},{lon_e}) off-lattice — masks/grid mismatch")
        if 0 <= ic < ni and 0 <= ir < nj:
            out[(lat, lon_e)] = float(values[ir * ni + ic])
    if not out:
        raise RuntimeError("no mask cells inside the grid")
    return out


def basin_means(vals, basins):
    out = {}
    for bid, b in basins.items():
        num = wsum = 0.0
        for c in b["cells"]:
            v = vals.get((c["lat"], c["lon_e"]))
            if v is None or v < 0.0 or v >= BIG:
                continue
            num += c["w"] * v
            wsum += c["w"]
        if wsum >= MIN_VALID:
            out[bid] = (num / wsum, wsum)
    return out


def fetch_hour(when):
    url = URL.format(y=when.year, m=when.month, d=when.day, h=when.hour)
    req = urllib.request.Request(url, headers={"User-Agent": "WCU-NOAH-helene/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


# --------------------------------------------------------------------------- #
def _demo_masks():
    """Stand-in masks so --selftest runs anywhere. NOT the real basin geometry."""
    return {"DEMO-A": {"area_sqmi": 1.0,
                       "cells": [{"lat": 35.30, "lon_e": 276.90, "w": 0.25},
                                 {"lat": 35.31, "lon_e": 276.90, "w": 0.25},
                                 {"lat": 35.30, "lon_e": 276.91, "w": 0.25},
                                 {"lat": 35.31, "lon_e": 276.91, "w": 0.25}]},
            "DEMO-B": {"area_sqmi": 2.0,
                       "cells": [{"lat": 35.32, "lon_e": 276.90, "w": 0.60},
                                 {"lat": 35.32, "lon_e": 276.91, "w": 0.40}]}}


def selftest():
    """Offline. No network, no eccodes. Exercises everything except the GRIB decode."""
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))

    print("helene_mrms_reconstruct — offline self-test")
    print("=" * 78)
    try:
        basins, src = load_masks(), MASKS
    except Exception as e:                                 # noqa: BLE001
        basins, src = _demo_masks(), f"DEMO MASKS ({type(e).__name__})"
    real = not src.startswith("DEMO")
    print(f"masks: {len(basins)} basins from {src}\n")

    print("mask integrity")
    worst = max(abs(sum(c["w"] for c in b["cells"]) - 1.0) for b in basins.values())
    chk("cell weights normalise to 1.0", worst <= 1e-2, f"worst {worst:.1e}")
    chk("campus basin present", (not real) or "CC-WCU-2260" in basins)

    print("\nweighting math")
    cells = _wanted(basins)
    m = basin_means({c: 25.4 for c in cells}, basins)         # a uniform 1.00 in
    err = max(abs(v[0] - 25.4) for v in m.values())
    chk("uniform field returns exactly that value", err < 1e-6, f"max err {err:.2e} mm")
    chk("every basin reported on a full field", len(m) == len(basins))

    print("\nfail direction — bad cells must not read as dry")
    bid0 = next(iter(basins))
    poisoned = {c: 25.4 for c in cells}
    for c in [(x["lat"], x["lon_e"]) for x in basins[bid0]["cells"][:1]]:
        poisoned[c] = -3.0                                    # MRMS no-coverage sentinel
    pm = basin_means(poisoned, basins)
    if bid0 in pm:
        chk("sentinel cell dropped, remainder renormalised",
            abs(pm[bid0][0] - 25.4) < 1e-6,
            f"got {pm[bid0][0]:.4f} mm (a zero-fill would give < 25.4)")
    else:
        chk("basin omitted when coverage falls below the gate", True,
            f"{bid0} withheld, valid fraction too low")
    gone = basin_means({}, basins)
    chk("no valid cells anywhere -> basins OMITTED, never reported as 0.00",
        gone == {}, f"returned {len(gone)} basins")

    print("\nridge table")
    ws = [w for _, w, _ in RIDGE]
    chk("monotone: more rain -> less wetness required", ws == sorted(ws, reverse=True))
    chk("brackets basins.py ARC-III (w 0.80-1.00)", max(ws) >= 0.80 and min(ws) < 0.80)
    chk("brackets permeability note (w 0.15-0.30)", min(ws) <= 0.15)
    chk("K24A's own 7.22 in is below the floor",
        K24A_TOTAL_IN < FLOOR_IN,
        f"{K24A_TOTAL_IN} < {FLOOR_IN} — the basins must have out-rained the valley")
    chk("below the floor returns no number", "OFF-RIDGE" in ridge_w(FLOOR_IN - 0.01))
    mid = float(ridge_w(9.86).lstrip("~<"))
    chk("interpolation stays inside its bracket", 0.248 <= mid <= 0.300,
        f"9.86 in -> w {mid:.3f}, bracket [0.248, 0.300]")

    print("\n" + "=" * 78)
    print("SELF-TEST " + ("PASSED" if ok else "FAILED"))
    if not real:
        print("NOTE: ran on demo masks. The math is verified; the GEOMETRY is not.")
        print("Put ledger/mrms_masks.json next to this script and re-run.")
    if not _DECODER_OK:
        print("NOTE: eccodes not importable here, so GRIB decoding was not exercised.")
        print("      pip install eccodes  before the real run.")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default=DEFAULT_START.strftime("%Y-%m-%dT%H"))
    ap.add_argument("--end", default=DEFAULT_END.strftime("%Y-%m-%dT%H"))
    ap.add_argument("--csv", default="helene_basin_rain.csv")
    ap.add_argument("--selftest", action="store_true",
                    help="offline checks, no network and no eccodes needed")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if not _DECODER_OK:
        sys.exit("eccodes not importable — pip install eccodes")

    start = dt.datetime.strptime(a.start, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc)
    end = dt.datetime.strptime(a.end, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc)
    basins = load_masks()
    order = list(basins)

    hours = int((end - start).total_seconds() // 3600)
    print(f"Helene reconstruction — MultiSensor_QPE_01H_Pass2 (gauge-corrected)")
    print(f"window {start:%Y-%m-%d %H}Z .. {end:%Y-%m-%d %H}Z  ({hours} hours)")
    print(f"masks  {len(basins)} basins\n")

    rows, totals, got, missed = [], {b: 0.0 for b in order}, 0, []
    for k in range(hours):
        when = start + dt.timedelta(hours=k)
        try:
            gz = fetch_hour(when)
        except urllib.error.HTTPError as e:
            missed.append((when, f"HTTP {e.code}")); continue
        except Exception as e:                             # noqa: BLE001
            missed.append((when, type(e).__name__)); continue
        try:
            means = basin_means(grid_values(gz, basins), basins)
        except Exception as e:                             # noqa: BLE001
            missed.append((when, f"decode: {e}")); continue
        got += 1
        for bid, (mm, vf) in means.items():
            totals[bid] += mm
            rows.append({"valid_utc": when.strftime("%Y-%m-%dT%H:00:00Z"),
                         "basin_id": bid, "qpe_mm": round(mm, 3),
                         "qpe_in": round(mm / 25.4, 4), "valid_frac": round(vf, 4)})
        wet = {b: round(m / 25.4, 2) for b, (m, _) in means.items() if m / 25.4 >= 0.05}
        print(f"  {when:%m-%d %H}Z  {len(means)}/8" + (f"  {wet}" if wet else ""))

    if not got:
        sys.exit("\nno hours retrieved — check network access to mtarchive")

    with open(a.csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["valid_utc", "basin_id", "qpe_mm",
                                          "qpe_in", "valid_frac"])
        w.writeheader(); w.writerows(rows)

    # ---------------------------------------------------------------- results
    print("\n" + "=" * 78)
    print(f"EVENT TOTALS  ({got}/{hours} hours retrieved"
          + (f", {len(missed)} missing" if missed else "") + ")")
    print("=" * 78)
    print(f"{'basin':<16}{'mm':>9}{'inches':>10}{'vs K24A 7.22':>15}")
    print("-" * 78)
    tin = {}
    for bid in order:
        inches = totals[bid] / 25.4
        tin[bid] = inches
        print(f"{bid:<16}{totals[bid]:>9.1f}{inches:>10.2f}"
              f"{inches - K24A_TOTAL_IN:>+15.2f}")
    lo, hi = min(tin.values()), max(tin.values())
    mean = sum(tin.values()) / len(tin)
    print("-" * 78)
    print(f"{'basin-mean':<16}{'':>9}{mean:>10.2f}")
    print(f"{'spread':<16}{'':>9}{hi - lo:>10.2f}   ({lo:.2f} .. {hi:.2f} in, "
          f"{100*(hi-lo)/mean:.0f}% of the mean)")

    # ------------------------------------------------- Q1: the ridge collapses
    print("\n" + "=" * 78)
    print("QUESTION 1 — was Helene a dry- or wet-antecedent event?  (PRELIMINARY)")
    print("=" * 78)
    print("  Ridge under the K24A hourly shape rescaled to each total. Read this as an")
    print("  orientation only: shape moves the answer as much as total does, and the")
    print("  measured shape is in the CSV this script just wrote. The number that")
    print("  counts comes from helene_solve_wetness.py.\n")
    print(f"    {'basin rainfall':>18}{'implied wetness w':>22}   {'record it supports':<22}")
    for tot, wv, rec in RIDGE:
        print(f"    {tot:>15.2f} in{wv:>22.3f}   {rec:<22}")
    camp = tin.get("CC-WCU-2260")
    if camp:
        print(f"\n  MEASURED campus-basin rainfall: {camp:.2f} in "
              f"({camp - K24A_TOTAL_IN:+.2f} vs the valley gauge)")
        if camp < FLOOR_IN:
            verdict = (f"BELOW the {FLOOR_IN} in floor. Even saturated soil cannot "
                       "reproduce the surveyed peak from this much rain, under this "
                       "shape. Either the radar is low (20-33% is documented for this "
                       "terrain) or the model under-produces. Both are findings, and "
                       "the sensitivity table below separates them.")
        elif camp <= WET_MAX_IN:
            verdict = ("the WET end. If the measured shape agrees, Helene was a "
                       "wet-antecedent event, the 'drought-dry' reading in the "
                       "permeability note is wrong, and basins.py's ARC-III is right.")
        elif camp >= DRY_MIN_IN:
            verdict = ("the DRY end. The 'drought-dry' reading holds and basins.py's "
                       "ARC-III record should be corrected.")
        else:
            verdict = ("the MIDDLE — w roughly 0.3-0.7, i.e. ordinary antecedent "
                       "conditions. BOTH existing records would be wrong.")
        print(f"  -> Preliminary: {verdict}")
    else:
        print("\n  NO CAMPUS BASIN IN THE RESULT. Nothing can be concluded about the")
        print("  anchor. Fix the mask coverage before reading anything else here.")

    # ------------------------------------------- Q2: lockstep, physics or not
    print("\n" + "=" * 78)
    print("QUESTION 2 — is the lockstep physics, or an artifact of point forcing?")
    print("=" * 78)
    print(f"  Measured basin-to-basin spread on the defining event: "
          f"{hi - lo:.2f} in ({100*(hi-lo)/mean:.0f}% of the mean)")
    print("  The live path forces all eight from Open-Meteo point queries at basin")
    print("  centroids, which collapse to one or two model grid cells.")
    if (hi - lo) / mean < 0.05:
        print("  -> Spread is small. Lockstep is largely PHYSICAL for this event; the")
        print("     point-forcing criticism is weaker than assumed. Note this is one")
        print("     event, and a widespread tropical system is the case most likely to")
        print("     be spatially uniform. Convective events will differ.")
    else:
        print("  -> Spread is REAL and the point forcing is discarding it. This is")
        print("     direct evidence that basin-averaged QPE would separate the basins,")
        print("     and that the near-zero spread the live path shows is an artifact.")

    # ------------------------------------------------------ honesty about bias
    print("\n" + "=" * 78)
    print("SENSITIVITY — read before concluding")
    print("=" * 78)
    print(f"  {'assumed radar low by':>22}{'campus rainfall':>18}{'implied w':>22}")
    print("  " + "-" * 62)
    if camp:
        for bias in (0.0, 0.10, 0.20, 0.33):
            print(f"  {bias*100:>20.0f}%{camp/(1.0-bias):>18.2f}"
                  f"{ridge_w(camp/(1.0-bias)):>22}")
    print("\n  Radar ran 20-33% low against gauges during Helene in this terrain, and")
    print("  Pass2's gauge correction depends on gauges that were themselves failing.")
    print("  If the verdict flips across this table, the answer is NOT SETTLED and the")
    print("  honest conclusion is that the anchor stays a ridge until in-basin gauges")
    print("  exist. Say so rather than picking the row you prefer.")

    if missed:
        print("\n" + "=" * 78)
        print(f"MISSING HOURS ({len(missed)}) — totals are a LOWER BOUND")
        print("=" * 78)
        for when, why in missed[:12]:
            print(f"  {when:%Y-%m-%d %H}Z  {why}")
        if len(missed) > 12:
            print(f"  ... and {len(missed)-12} more")

    print(f"\nwrote {len(rows)} rows -> {a.csv}")
    print("Next: feed the hourly per-basin series into cwm_model.assess_event() and")
    print("solve for w. That is the number the whole calibration hangs on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
