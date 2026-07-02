#!/usr/bin/env python3
"""
fetch_mrms.py — archive observed basin-mean rainfall from MRMS, hourly.
=============================================================================
Truth side of the QPF-bias ledger. Uses MultiSensor_QPE_01H_Pass2
(gauge-corrected, ~1 h product latency — this is a verification ledger, not a
trigger path, so quality beats latency) from the Iowa State mtarchive.

PIPELINE per hour:
  1. download  MultiSensor_QPE_01H_Pass2_00.00_YYYYMMDD-HH0000.grib2.gz
  2. gunzip in memory
  3. eccodes decodes the GRIB2 message from bytes (no temp files)
  4. direct index math pulls ONLY the ~90 cells in ledger/mrms_masks.json
     from the CONUS grid (regular lat-lon; row/col computed from the grid
     header each time — never assumed)
  5. weighted mean per basin
  6. INSERT into observations (mm)

DECODER NOTE: wgrib2 is not packaged for Ubuntu 24.04, so this uses ECMWF
eccodes instead (`pip install eccodes` — self-contained wheel, bundles the
C library, pulls numpy). Cleaner anyway: no subprocess, no bbox clip.

MISSING DATA: MRMS flags no-coverage/missing as negative values (and eccodes
may surface encoded missing as a large sentinel). Such cells are dropped and
remaining weights renormalized; valid_frac records the surviving weight
fraction. If valid_frac < MIN_VALID for a basin, that basin-hour is skipped
(better a gap than a fabricated mean). Analysis should additionally filter
min_valid_frac >= 0.8.

GAP REPAIR: each run first processes the target hour (now - LAG), then sweeps
the previous LOOKBACK_H hours and fills any basin-hours missing from the DB
(mtarchive occasionally posts late). A 404 is logged and left for the next
sweep — the unit exits 0 so systemd does not flap.

Run: hourly from systemd (deploy/qpf-mrms.timer), or by hand:
    python3 fetch_mrms.py [--db PATH] [--hour YYYY-MM-DDTHH] [--no-sweep]
Deps: eccodes (pip), numpy (pulled by eccodes). Run inside the project venv.
"""

import argparse
import datetime as dt
import gzip
import json
import os
import sys
import urllib.request

import ledger_db

try:
    import eccodes
    _DECODER_OK = True
except Exception:                                     # noqa: BLE001
    _DECODER_OK = False

MASKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "mrms_masks.json")
URL = ("https://mtarchive.geol.iastate.edu/{y:04d}/{m:02d}/{d:02d}/mrms/ncep/"
       "MultiSensor_QPE_01H_Pass2/"
       "MultiSensor_QPE_01H_Pass2_00.00_{y:04d}{m:02d}{d:02d}-{h:02d}0000.grib2.gz")
SOURCE = "mrms-p2"
LAG_H = 2          # Pass2 latency margin
LOOKBACK_H = 48    # gap-repair sweep depth
MIN_VALID = 0.5    # minimum surviving weight fraction to accept a basin-hour
BIG = 1.0e10       # anything at/above this is an encoded-missing sentinel


def load_masks():
    with open(MASKS_FILE) as f:
        m = json.load(f)
    return m["basins"], m.get("bbox_wgrib2")


def decoder_available():
    return _DECODER_OK


def _wanted_cells(basins):
    """Union of (lat, lon_e) cell centers across all basin masks."""
    cells = set()
    for b in basins.values():
        for c in b["cells"]:
            cells.add((c["lat"], c["lon_e"]))
    return cells


def grid_values(grib_gz_bytes, basins):
    """gz GRIB2 bytes -> {(lat, lon_e): value} for exactly the mask cells.

    Grid geometry (origin, increments, scan direction, row-major order) is
    read from the message header on every call — nothing about the MRMS grid
    is hard-coded, so a grid change upstream fails loudly instead of silently
    misregistering."""
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
        values = eccodes.codes_get_values(gid)        # 1-D, row-major (j, i)
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
                f"cell ({lat},{lon_e}) off-lattice for this grid "
                f"(col {col:.3f}, row {row:.3f}) — masks/grid mismatch")
        if 0 <= ic < ni and 0 <= ir < nj:
            out[(lat, lon_e)] = float(values[ir * ni + ic])
    if not out:
        raise RuntimeError("no mask cells fell inside the GRIB grid")
    return out


def basin_means(vals, basins):
    """-> {basin_id: (qpe_mm, valid_frac)} using renormalized cell weights."""
    out = {}
    for bid, b in basins.items():
        num = wsum = 0.0
        for c in b["cells"]:
            v = vals.get((c["lat"], c["lon_e"]))
            if v is None or v < 0.0 or v >= BIG:      # missing / no coverage
                continue
            num += c["w"] * v
            wsum += c["w"]
        if wsum >= MIN_VALID:
            out[bid] = (num / wsum, wsum)
    return out


def process_hour(conn, when, basins, quiet=False):
    """Fetch + ingest one valid hour (accumulation ending at `when`, UTC).
    Returns True on success, False if the file is not (yet) available."""
    url = URL.format(y=when.year, m=when.month, d=when.day, h=when.hour)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "WCU-NOAH-qpf-ledger/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            gz = r.read()
    except urllib.error.HTTPError as e:
        if not quiet:
            print(f"  {when:%Y-%m-%dT%H}Z: HTTP {e.code} (not posted yet?)")
        return False
    except Exception as e:                            # noqa: BLE001
        if not quiet:
            print(f"  {when:%Y-%m-%dT%H}Z: fetch error: {e}")
        return False

    vals = grid_values(gz, basins)
    means = basin_means(vals, basins)
    valid = when.strftime("%Y-%m-%dT%H:00:00")
    ledger_db.insert_observations(
        conn, [(bid, valid, round(q, 3), round(vf, 4), SOURCE)
               for bid, (q, vf) in means.items()])
    if not quiet:
        wet = {b: round(q, 1) for b, (q, _) in means.items() if q >= 0.1}
        print(f"  {valid}Z: {len(means)}/8 basins"
              + (f"  wet: {wet}" if wet else ""))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--hour", default=None,
                    help="specific valid hour YYYY-MM-DDTHH (UTC); "
                         "default = now - LAG_H")
    ap.add_argument("--no-sweep", action="store_true",
                    help="skip the gap-repair lookback sweep")
    args = ap.parse_args()

    if not decoder_available():
        sys.exit("eccodes not importable — pip install eccodes "
                 "(inside the project venv)")

    basins, _ = load_masks()
    conn = ledger_db.connect(args.db)

    if args.hour:
        target = dt.datetime.strptime(args.hour, "%Y-%m-%dT%H").replace(
            tzinfo=dt.timezone.utc)
    else:
        target = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(hours=LAG_H)).replace(
            minute=0, second=0, microsecond=0)

    print(f"target hour {target:%Y-%m-%dT%H}Z")
    process_hour(conn, target, basins)

    if not args.no_sweep and not args.hour:
        filled = 0
        for k in range(1, LOOKBACK_H + 1):
            when = target - dt.timedelta(hours=k)
            valid = when.strftime("%Y-%m-%dT%H:00:00")
            if ledger_db.have_observation(conn, valid, SOURCE):
                continue
            if process_hour(conn, when, basins, quiet=True):
                filled += 1
        if filled:
            print(f"gap sweep: back-filled {filled} hour(s)")
    conn.close()


if __name__ == "__main__":
    main()
