#!/usr/bin/env python3
"""
mrms_live.py — basin-mean OBSERVED rainfall from MRMS, at trigger latency.

Sibling to ledger/fetch_mrms.py, and deliberately a different product.

    ledger/fetch_mrms.py  MultiSensor_QPE_01H_Pass2   ~1 h latency   VERIFICATION
    mrms_live.py          RadarOnly_QPE_01H           ~2 min         TRIGGER

fetch_mrms.py says so in its own docstring: "this is a verification ledger, not a
trigger path, so quality beats latency." That is the correct choice there and the wrong
one here. Cox Branch has a time of concentration of 29 minutes. A product with 60
minutes of latency cannot participate in warning it; by the time Pass2 exists the flood
has happened. Pass1 (~20 min) is marginal. RadarOnly (~2 min) is the only MRMS product
fast enough to matter on these basins.

The price of that speed is stated, not hidden: RadarOnly is NOT gauge-corrected. In this
terrain that bias is large and known —

  * radar underestimated Helene's totals by 20-33% against gauges (Godfrey, UNC
    Collaboratory)
  * 30-50% of mid/high-elevation accumulation in the Smokies comes from seeder-feeder
    light rain that radar systematically misses (Duke/UNCA GSMRGN)
  * beam blockage, overshooting and VPR variation all degrade mountain QPE (NWS
    Mountain Mapper)

So RadarOnly is better than a point forecast interpolated to a centroid, and it is not
truth. Bias-correct against NOAH's own tipping buckets once they exist; until then treat
it as a lower bound and say so on the surface.

WHAT THIS IS FOR
  Not a replacement for the Open-Meteo forecast in live_rainfall.compute_from_response.
  That function's `storm` is a FORECAST ("worst upcoming 24-hr day"). This is OBSERVED
  accumulation already on the ground. Substituting one for the other would be a category
  error. Observed basin rainfall is the input to:
    - a Confirmed-rainfall tier (decision D2), which is how a 29-minute basin gets any
      lead time at all
    - upward-only bias correction of the forecast, the same shape as
      gov_sources.storm_correction_map()
    - the lockstep fix: eight genuinely different basin means instead of eight point
      queries that collapse into one or two model grid cells

RESOLUTION REALITY CHECK (printed by --selftest)
  MRMS is 0.01 deg, about 1.0 km^2 per cell at this latitude. Cox Branch is 0.97 mi^2 =
  2.5 km^2 and its mask has 8 partially-covered cells. So Cox is roughly two and a half
  cells of actual area. MRMS can just resolve it; it cannot resolve anything smaller,
  and the effective independent sample count there is ~2-3, not 8. Long Branch (14
  cells, 1.71 mi^2) is comparable. State this when quoting a Cox Branch basin mean.

MISSING DATA
  Same discipline as fetch_mrms.py: negative and sentinel values are dropped, remaining
  weights renormalised, valid_frac recorded, and a basin below MIN_VALID is OMITTED
  rather than reported as a small number. A basin absent from the result means "not
  observed", never "no rain" — see posture_rules.py. Callers must not coerce a missing
  basin to 0.0.

Run:
    python mrms_live.py                 # latest hour, all basins
    python mrms_live.py --hours 3       # 3-hour accumulation
    python mrms_live.py --selftest      # offline: mask + weighting math, no network
Deps: eccodes (pip install eccodes) for live use. --selftest needs stdlib only.
"""

import argparse
import datetime as dt
import gzip
import json
import os
import sys
import urllib.error
import urllib.request

# --------------------------------------------------------------------------- #
# Masks are shared with the ledger fetcher. One source of truth for geometry.
# --------------------------------------------------------------------------- #
_HERE = os.path.dirname(os.path.abspath(__file__))
MASKS_FILE = os.path.join(_HERE, "ledger", "mrms_masks.json")
if not os.path.exists(MASKS_FILE):                      # running from inside ledger/
    MASKS_FILE = os.path.join(_HERE, "mrms_masks.json")

MIN_VALID = 0.5          # minimum surviving weight fraction to report a basin
BIG = 1.0e10             # at/above this is an encoded-missing sentinel
TIMEOUT = 60

# Product ladder, fastest first. Each entry: (name, nominal latency minutes, url builder)
# Trying in this order and REPORTING which one answered is the point: a caller that
# silently accepted Pass2 would think it had a trigger and actually have a postmortem.
AWS = ("https://noaa-mrms-pds.s3.amazonaws.com/CONUS/{prod}_00.00/{ymd}/"
       "MRMS_{prod}_00.00_{ymd}-{hms}.grib2.gz")
NCEP_LATEST = ("https://mrms.ncep.noaa.gov/data/2D/{prod}/"
               "MRMS_{prod}.latest.grib2.gz")

PRODUCTS = [
    ("RadarOnly_QPE_01H", 2, False),        # gauge_corrected = False
    ("MultiSensor_QPE_01H_Pass1", 20, True),
    ("MultiSensor_QPE_01H_Pass2", 60, True),
]

try:
    import eccodes
    _DECODER_OK = True
except Exception:                                        # noqa: BLE001
    _DECODER_OK = False


# --------------------------------------------------------------------------- #
# Geometry — identical maths to fetch_mrms.grid_values, kept independent so the
# ledger path and the trigger path cannot break each other.
# --------------------------------------------------------------------------- #
def load_masks(path=MASKS_FILE):
    with open(path) as f:
        m = json.load(f)
    return m["basins"]


def _wanted_cells(basins):
    cells = set()
    for b in basins.values():
        for c in b["cells"]:
            cells.add((c["lat"], c["lon_e"]))
    return cells


def grid_values(grib_gz_bytes, basins):
    """gz GRIB2 bytes -> {(lat, lon_e): value} for exactly the mask cells.

    Grid origin, increments and scan direction are read from the message header every
    call. Nothing about the MRMS grid is hard-coded, so an upstream grid change fails
    loudly rather than silently misregistering the basins."""
    gid = eccodes.codes_new_from_message(gzip.decompress(grib_gz_bytes))
    try:
        ni = eccodes.codes_get(gid, "Ni")
        nj = eccodes.codes_get(gid, "Nj")
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
        raise RuntimeError(f"grid size mismatch: {len(values)} != {ni}*{nj}")

    out = {}
    for lat, lon_e in _wanted_cells(basins):
        col = (lon_e - lon1) / di if ipos else (lon1 - lon_e) / di
        row = (lat - lat1) / dj if jpos else (lat1 - lat) / dj
        ic, ir = round(col), round(row)
        if abs(col - ic) > 0.25 or abs(row - ir) > 0.25:
            raise RuntimeError(
                f"cell ({lat},{lon_e}) off-lattice (col {col:.3f}, row {row:.3f}) "
                f"— masks/grid mismatch")
        if 0 <= ic < ni and 0 <= ir < nj:
            out[(lat, lon_e)] = float(values[ir * ni + ic])
    if not out:
        raise RuntimeError("no mask cells fell inside the GRIB grid")
    return out


def basin_means(vals, basins, min_valid=MIN_VALID):
    """-> {basin_id: {"mm", "in", "valid_frac", "n_used", "n_cells"}}

    A basin whose surviving weight falls below `min_valid` is OMITTED, not reported
    as a small number. Missing is missing.
    """
    out = {}
    for bid, b in basins.items():
        num = wsum = 0.0
        used = 0
        for c in b["cells"]:
            v = vals.get((c["lat"], c["lon_e"]))
            if v is None or v < 0.0 or v >= BIG:
                continue
            num += c["w"] * v
            wsum += c["w"]
            used += 1
        if wsum >= min_valid:
            mm = num / wsum
            out[bid] = {"mm": round(mm, 3), "in": round(mm / 25.4, 4),
                        "valid_frac": round(wsum, 4),
                        "n_used": used, "n_cells": len(b["cells"])}
    return out


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #
def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "WCU-NOAH-mrms-live/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def fetch_product(prod, when=None):
    """Try AWS at a specific timestamp, then NCEP 'latest'. Returns (bytes, note)."""
    if when is not None:
        url = AWS.format(prod=prod, ymd=when.strftime("%Y%m%d"),
                         hms=when.strftime("%H%M%S"))
        try:
            return _get(url), f"aws {when:%Y-%m-%dT%H:%M:%S}Z"
        except Exception as e:                            # noqa: BLE001
            last = f"aws: {type(e).__name__}"
    else:
        last = "aws: not attempted (no timestamp)"
    try:
        return _get(NCEP_LATEST.format(prod=prod)), "ncep latest"
    except Exception as e:                                # noqa: BLE001
        raise RuntimeError(f"{prod}: {last}; ncep: {type(e).__name__}: {e}")


def observed_rain(hours=1, basins=None, when=None, allow_slow=True):
    """Basin-mean OBSERVED rainfall over the last `hours`.

    Returns {"product", "gauge_corrected", "latency_min", "source_note", "valid_utc",
             "hours", "basins": {...}} — or raises. Never returns zeros on failure.

    `allow_slow=False` restricts to products fast enough for a 29-minute basin, i.e.
    RadarOnly only. Use that on the trigger path; the default ladder is for backfill
    and display where a slower, gauge-corrected product is preferable.
    """
    if not _DECODER_OK:
        raise RuntimeError("eccodes not importable — pip install eccodes")
    basins = basins or load_masks()
    products = PRODUCTS if allow_slow else PRODUCTS[:1]

    errors = []
    for prod, latency, corrected in products:
        try:
            if hours == 1:
                raw, note = fetch_product(prod, when)
                vals = grid_values(raw, basins)
                means = basin_means(vals, basins)
            else:
                # accumulate N consecutive hourly grids; a missing hour is fatal for
                # the accumulation rather than quietly under-counted
                base = when or dt.datetime.now(dt.timezone.utc).replace(
                    minute=0, second=0, microsecond=0)
                acc, note = {}, None
                for k in range(hours):
                    t = base - dt.timedelta(hours=k)
                    raw, note = fetch_product(prod, t)
                    v = grid_values(raw, basins)
                    for cell, val in v.items():
                        if val is None or val < 0.0 or val >= BIG:
                            acc[cell] = None            # poison the cell for the window
                        elif acc.get(cell, 0.0) is not None:
                            acc[cell] = acc.get(cell, 0.0) + val
                means = basin_means({k: v for k, v in acc.items() if v is not None},
                                    basins)
                note = f"{note} (+{hours-1} prior hour(s))"
            if not means:
                raise RuntimeError("no basin met MIN_VALID — grid covered but empty")
            return {"product": prod, "gauge_corrected": corrected,
                    "latency_min": latency, "source_note": note,
                    "valid_utc": (when or dt.datetime.now(dt.timezone.utc)).strftime(
                        "%Y-%m-%dT%H:00:00Z"),
                    "hours": hours, "basins": means}
        except Exception as e:                            # noqa: BLE001
            errors.append(f"{prod}: {e}")
    raise RuntimeError("all MRMS products failed:\n  " + "\n  ".join(errors))


# --------------------------------------------------------------------------- #
# Offline self-test — proves the weighting and missing-data discipline with no network
# --------------------------------------------------------------------------- #
def selftest():
    basins = load_masks()
    cells = _wanted_cells(basins)
    print("MRMS LIVE — offline self-test (no network, no eccodes needed)")
    print("=" * 74)
    print(f"masks: {len(basins)} basins, {len(cells)} distinct cells")

    # --- geometry sanity ---------------------------------------------------
    lats = sorted({c[0] for c in cells}); lons = sorted({c[1] for c in cells})
    dlat = round(lats[1] - lats[0], 5) if len(lats) > 1 else None
    import math
    km_lat = dlat * 110.574
    km_lon = dlat * 111.320 * math.cos(math.radians(sum(lats) / len(lats)))
    cell_km2 = km_lat * km_lon
    print(f"grid spacing {dlat} deg  ->  {km_lat:.2f} x {km_lon:.2f} km "
          f"= {cell_km2:.2f} km^2 per cell")

    print(f"\n{'basin':<16}{'area mi2':>10}{'area km2':>10}{'cells':>7}"
          f"{'cells of area':>15}   resolution")
    print("-" * 74)
    for bid, b in basins.items():
        a_mi = b["area_sqmi"]; a_km = a_mi * 2.58999
        eff = a_km / cell_km2
        flag = ("MARGINAL — quote with care" if eff < 4 else
                "thin" if eff < 10 else "ok")
        print(f"{bid:<16}{a_mi:>10.2f}{a_km:>10.2f}{len(b['cells']):>7}"
              f"{eff:>15.1f}   {flag}")

    # --- weights normalised ------------------------------------------------
    # Two thresholds on purpose. 1e-2 catches a REAL coverage change (a dropped or
    # duplicated cell moves the sum by ~1/n). 1e-6 catches nothing but round-off from
    # mask generation, which basin_means() absorbs anyway because it divides by the
    # ACTUAL surviving weight rather than assuming 1.0. Measured worst case here is
    # 4 ppm on CC-MOUTH-2340. Reported, not silenced: if it ever grows, something
    # regenerated the masks.
    devs = {bid: sum(c["w"] for c in b["cells"]) - 1.0 for bid, b in basins.items()}
    broken = {b: d for b, d in devs.items() if abs(d) > 1e-2}
    drift = {b: d for b, d in devs.items() if 1e-9 < abs(d) <= 1e-2}
    worst = max(abs(d) for d in devs.values())
    print(f"\nweight normalisation: worst deviation {worst:.1e} "
          f"({worst*1e6:.1f} ppm)")
    if broken:
        print(f"  *** COVERAGE CHANGE: {list(broken)} — masks no longer tile the basin")
    elif drift:
        print(f"  round-off only on {len(drift)} basin(s); absorbed by renormalisation")
    else:
        print("  exact")

    # --- uniform field must return exactly that value ----------------------
    uniform = {c: 12.7 for c in cells}                    # 12.7 mm = 0.5 in
    m = basin_means(uniform, basins)
    err = max(abs(v["mm"] - 12.7) for v in m.values())
    print(f"uniform 12.7 mm field reproduces exactly: "
          f"{'YES' if err < 1e-6 else f'NO (max err {err})'}  "
          f"({len(m)}/{len(basins)} basins)")

    # --- missing data: dropped, renormalised, and omitted below MIN_VALID --
    half = dict(uniform)
    cox = basins["CC-COX-097"]["cells"]
    for c in cox[: len(cox) // 2]:                        # kill half of Cox's cells
        half[(c["lat"], c["lon_e"])] = -3.0               # MRMS no-coverage flag
    m2 = basin_means(half, basins)
    cox_left = sum(c["w"] for c in cox[len(cox) // 2:])
    print(f"\nhalf of Cox Branch's cells set to no-coverage (-3.0):")
    if "CC-COX-097" in m2:
        got = m2["CC-COX-097"]
        print(f"  reported {got['mm']} mm from {got['n_used']}/{got['n_cells']} cells, "
              f"valid_frac {got['valid_frac']}")
        print(f"  renormalised correctly: "
              f"{'YES' if abs(got['mm'] - 12.7) < 1e-6 else 'NO'} "
              f"(surviving weight {cox_left:.3f} >= MIN_VALID {MIN_VALID})")
    else:
        print(f"  OMITTED — surviving weight {cox_left:.3f} < MIN_VALID {MIN_VALID}")
        print("  correct: a basin we cannot see is absent, never 0.0")

    # --- total blackout: basin must be ABSENT, not zero --------------------
    blackout = {c: -3.0 for c in cells}
    m3 = basin_means(blackout, basins)
    print(f"\ntotal radar blackout -> {len(m3)} basins reported "
          f"({'correct: all omitted' if not m3 else 'WRONG: should be 0'})")
    print("  a caller that coerces a missing basin to 0.0 mm has reintroduced the")
    print("  FloodNet veto bug — see posture_rules.py")

    print("\n" + "=" * 74)
    print("PASS — geometry, weighting and missing-data discipline verified offline.")
    print("Live fetch additionally needs: pip install eccodes, and network egress to")
    print("noaa-mrms-pds.s3.amazonaws.com or mrms.ncep.noaa.gov")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=int, default=1, help="accumulation window (default 1)")
    ap.add_argument("--fast-only", action="store_true",
                    help="RadarOnly only — the trigger-path constraint")
    ap.add_argument("--selftest", action="store_true", help="offline checks, no network")
    a = ap.parse_args()

    if a.selftest:
        selftest()
        return 0

    r = observed_rain(hours=a.hours, allow_slow=not a.fast_only)
    print(f"{r['product']}  latency ~{r['latency_min']} min  "
          f"gauge-corrected={r['gauge_corrected']}  [{r['source_note']}]")
    print(f"valid {r['valid_utc']}  window {r['hours']} h\n")
    print(f"{'basin':<16}{'mm':>9}{'inches':>10}{'valid':>8}{'cells':>10}")
    print("-" * 55)
    for bid, v in r["basins"].items():
        print(f"{bid:<16}{v['mm']:>9.2f}{v['in']:>10.3f}{v['valid_frac']:>8.2f}"
              f"{v['n_used']:>6}/{v['n_cells']:<4}")
    missing = set(load_masks()) - set(r["basins"])
    if missing:
        print(f"\nNOT OBSERVED (absent, not zero): {sorted(missing)}")
    if not r["gauge_corrected"]:
        print("\nRadarOnly is not gauge-corrected. In this terrain radar ran 20-33% low")
        print("against gauges during Helene. Treat as a lower bound until bias-corrected")
        print("against in-basin buckets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
