#!/usr/bin/env python3
"""
fetch_mrms.py — archive observed basin-mean rainfall from MRMS, hourly.
=============================================================================
Truth side of the QPF-bias ledger. Uses MultiSensor_QPE_01H_Pass2
(gauge-corrected, ~1 h product latency — this is a verification ledger, not a
trigger path, so quality beats latency) from the Iowa State mtarchive.

PIPELINE per hour:
  1. download  MultiSensor_QPE_01H_Pass2_00.00_YYYYMMDD-HH0000.grib2.gz
  2. gunzip
  3. wgrib2 -small_grib <bbox>          clip CONUS to the watershed (tiny)
  4. wgrib2 -csv                        dump lon,lat,value of clipped cells
  5. weighted mean per basin from ledger/mrms_masks.json cell weights
  6. INSERT into observations (mm)

MISSING DATA: MRMS flags no-coverage/missing as negative values. Negative
cells are dropped and remaining weights renormalized; valid_frac records the
surviving weight fraction. If valid_frac < MIN_VALID for a basin, that
basin-hour is skipped (better a gap than a fabricated mean). Analysis should
additionally filter min_valid_frac >= 0.8.

GAP REPAIR: each run first processes the target hour (now - LAG), then sweeps
the previous LOOKBACK_H hours and fills any basin-hours missing from the DB
(mtarchive occasionally posts late). A 404 is logged and left for the next
sweep — the unit exits 0 so systemd does not flap.

Run: hourly from systemd (deploy/qpf-mrms.timer), or by hand:
    python3 fetch_mrms.py [--db PATH] [--hour YYYY-MM-DDTHH] [--no-sweep]
Deps: standard library + the `wgrib2` binary (apt install wgrib2).
"""

import argparse
import datetime as dt
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

import ledger_db

MASKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "mrms_masks.json")
URL = ("https://mtarchive.geol.iastate.edu/{y:04d}/{m:02d}/{d:02d}/mrms/ncep/"
       "MultiSensor_QPE_01H_Pass2/"
       "MultiSensor_QPE_01H_Pass2_00.00_{y:04d}{m:02d}{d:02d}-{h:02d}0000.grib2.gz")
SOURCE = "mrms-p2"
LAG_H = 2          # Pass2 latency margin
LOOKBACK_H = 48    # gap-repair sweep depth
MIN_VALID = 0.5    # minimum surviving weight fraction to accept a basin-hour


def load_masks():
    with open(MASKS_FILE) as f:
        m = json.load(f)
    return m["basins"], m["bbox_wgrib2"]


def wgrib2_available():
    return shutil.which("wgrib2") is not None


def grid_values(grib_gz_bytes, bbox, workdir):
    """gz GRIB2 bytes -> {(lat, lon_e): value} over the watershed bbox."""
    raw = os.path.join(workdir, "full.grib2")
    small = os.path.join(workdir, "small.grib2")
    csvf = os.path.join(workdir, "vals.csv")
    with open(raw, "wb") as f:
        f.write(gzip.decompress(grib_gz_bytes))
    subprocess.run(
        ["wgrib2", raw, "-small_grib",
         f"{bbox['lon_w']}:{bbox['lon_e']}", f"{bbox['lat_s']}:{bbox['lat_n']}",
         small],
        check=True, capture_output=True)
    subprocess.run(["wgrib2", small, "-csv", csvf],
                   check=True, capture_output=True)
    vals = {}
    with open(csvf) as f:
        for line in f:
            parts = line.rstrip("\n").split(",")
            if len(parts) < 3:
                continue
            try:
                lon, lat, v = (float(parts[-3]), float(parts[-2]),
                               float(parts[-1]))
            except ValueError:
                continue
            vals[(round(lat, 3), round(lon, 3))] = v
    if not vals:
        raise RuntimeError("wgrib2 produced no cell values")
    return vals


def basin_means(vals, basins):
    """-> {basin_id: (qpe_mm, valid_frac)} using renormalized cell weights."""
    out = {}
    for bid, b in basins.items():
        num = wsum = 0.0
        for c in b["cells"]:
            v = vals.get((c["lat"], c["lon_e"]))
            if v is None or v < 0.0:          # missing / no coverage
                continue
            num += c["w"] * v
            wsum += c["w"]
        if wsum >= MIN_VALID:
            out[bid] = (num / wsum, wsum)
    return out


def process_hour(conn, when, basins, bbox, quiet=False):
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

    with tempfile.TemporaryDirectory() as td:
        vals = grid_values(gz, bbox, td)
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

    if not wgrib2_available():
        sys.exit("wgrib2 not found — apt install wgrib2")

    basins, bbox = load_masks()
    conn = ledger_db.connect(args.db)

    if args.hour:
        target = dt.datetime.strptime(args.hour, "%Y-%m-%dT%H").replace(
            tzinfo=dt.timezone.utc)
    else:
        target = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(hours=LAG_H)).replace(
            minute=0, second=0, microsecond=0)

    print(f"target hour {target:%Y-%m-%dT%H}Z")
    process_hour(conn, target, basins, bbox)

    if not args.no_sweep and not args.hour:
        filled = 0
        for k in range(1, LOOKBACK_H + 1):
            when = target - dt.timedelta(hours=k)
            valid = when.strftime("%Y-%m-%dT%H:00:00")
            if ledger_db.have_observation(conn, valid, SOURCE):
                continue
            if process_hour(conn, when, basins, bbox, quiet=True):
                filled += 1
        if filled:
            print(f"gap sweep: back-filled {filled} hour(s)")
    conn.close()


if __name__ == "__main__":
    main()
