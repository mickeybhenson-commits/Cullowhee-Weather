#!/usr/bin/env python3
"""
fetch_forecast.py — archive the operational QPF input, per basin, every 6 h.
=============================================================================
Pulls the FULL hourly forecast horizon (7 days) from Open-Meteo for all eight
sub-basin points in ONE multi-location call and stores every (basin, valid
hour) atom stamped with the issuance time. Lead time is derived downstream
(valid - issued), so one fetch populates every lead bucket at once.

This archives the forecast SKYE actually consumes (Open-Meteo best_match) —
the bias we need first is the bias of the operational input, not of the best
available QPF.

Point-vs-areal caveat (logged in source tag): forecasts are point values at
the basin representative points; MRMS truth is basin-areal. Acceptable at
these basin sizes (0.97-23.4 mi^2 vs ~10 km model grids).

Run: every 6 h from systemd (deploy/qpf-forecast.timer), or by hand:
    python3 fetch_forecast.py [--db /path/to/qpf_ledger.db]
Deps: standard library only.
"""

import argparse
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request

import ledger_db

# Basin representative points — keep identical to live_rainfall.BASIN_POINTS.
BASIN_POINTS = {
    "CC-UP-503":     (35.241, -83.185),
    "CC-MS-1100":    (35.265, -83.190),
    "CC-TIL-705":    (35.268, -83.205),
    "CC-SPD-1830":   (35.270, -83.190),
    "CC-COX-097":    (35.302, -83.178),
    "CC-LB-171":     (35.305, -83.195),
    "CC-WCU-2260":   (35.290, -83.185),
    "CC-MOUTH-2340": (35.300, -83.185),
}

API = "https://api.open-meteo.com/v1/forecast"
SOURCE = "om-best"
FORECAST_DAYS = 7
RETRIES = 3


def fetch_all(basins):
    """One multi-location call; returns list of per-basin JSON dicts in the
    same order as `basins`."""
    ids = list(basins)
    qs = urllib.parse.urlencode({
        "latitude":  ",".join(f"{basins[b][0]:.3f}" for b in ids),
        "longitude": ",".join(f"{basins[b][1]:.3f}" for b in ids),
        "hourly": "precipitation",
        "forecast_days": FORECAST_DAYS,
        "timezone": "UTC",
    })
    req = urllib.request.Request(API + "?" + qs,
                                 headers={"User-Agent": "WCU-NOAH-qpf-ledger/1.0"})
    last = None
    for i in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            return data if isinstance(data, list) else [data], ids
        except Exception as e:                       # noqa: BLE001
            last = e
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"Open-Meteo fetch failed after {RETRIES} tries: {last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    issued = dt.datetime.now(dt.timezone.utc).replace(
        minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00:00")

    payloads, ids = fetch_all(BASIN_POINTS)
    if len(payloads) != len(ids):
        sys.exit(f"expected {len(ids)} locations, got {len(payloads)}")

    rows = []
    for bid, p in zip(ids, payloads):
        times = p["hourly"]["time"]
        precs = p["hourly"]["precipitation"]
        for t, v in zip(times, precs):
            if v is None:
                continue
            rows.append((bid, issued, t + ":00" if len(t) == 16 else t,
                         float(v), SOURCE))

    conn = ledger_db.connect(args.db)
    ledger_db.insert_forecasts(conn, rows)
    conn.close()
    print(f"issued={issued}Z  stored {len(rows)} forecast atoms "
          f"({len(ids)} basins x {FORECAST_DAYS}d hourly)")


if __name__ == "__main__":
    main()
