#!/usr/bin/env python3
"""
fetch_weathernext.py — archive WeatherNext 2 ensemble QPF into the ledger.
=============================================================================
Companion to fetch_forecast.py (Open-Meteo). Same DB, same verification
machinery: every archived atom lands in `forecasts` and is scored against
MRMS truth by the existing fc_6h view / bias analysis, no schema change.

WHY ARCHIVE THIS BEFORE THE OUTLOOK EVER GOES PUBLIC
  The 28-km WeatherNext cell cannot see orographic enhancement. The ONLY
  honest fix is a learned per-cell multiplier — and that needs a season of
  (forecast, observed) pairs in this ledger FIRST. Run this from day one,
  even while the outlook feed is dark: calendar time is the ingredient no
  code change can substitute. (Sequencing note from the integration review:
  ledger archiving ships first for exactly this reason.)

WHAT IS STORED (deliberately NOT all 64 members)
  Four summary series per basin per issuance, as ledger sources:
      wn2-mean   ensemble mean      <- the series the bias multiplier fits
      wn2-p10 / wn2-p50 / wn2-p90   spread, for sharpness/reliability checks
  64 members x 8 basins x 40 windows would be ~20k rows per fetch and the
  bias fit only needs the moments. Full members live in feed/outlook.json
  transiently; the ledger keeps the verifiable summary.

HOURLY DISAGGREGATION CAVEAT (read before trusting sub-6h numbers)
  The ledger's atom is HOURLY (fc_6h view keeps a window only when all six
  hourly atoms are present). WeatherNext is natively 6-hourly, so each
  window total is split UNIFORMLY into six equal hourly atoms. 6-h window
  sums — the only aggregation the bias analysis uses — are exact; the
  hourly values themselves are an artifact and must never be compared to
  hourly MRMS directly. The wn2-* source tag is the guard: any analysis
  joining hourly atoms must exclude source LIKE 'wn2-%'.

Run: alongside qpf-forecast.timer cadence (WeatherNext inits 4x/day and
disseminates 6-8 h late; a 6-h timer catches each init once):
    python3 fetch_weathernext.py [--db /path/to/qpf_ledger.db]
Deps: standard library only (weathernext_source resolves the actual source).
"""

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))          # ledger/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ledger_db
import weathernext_source as wn

IN_TO_MM = 25.4
SERIES = ("mean", "p10", "p50", "p90")


def summarize(member_windows):
    """Per-window mean/p10/p50/p90 (inches) across members.
    Returns {series_name: [value per window]}."""
    nwin = min(len(m) for m in member_windows)
    out = {s: [] for s in SERIES}
    for i in range(nwin):
        col = [m[i] for m in member_windows]
        out["mean"].append(round(sum(col) / len(col), 4))
        q = wn.quantiles(col)
        for s in ("p10", "p50", "p90"):
            out[s].append(q[s])
    return out


def hourly_rows(bid, issued_utc, valid_utc, summaries):
    """Ledger atoms: each 6-h window split into 6 equal HOURLY rows ending at
    the window end (matches the schema's 'value covers the preceding hour'
    convention; window membership in fc_6h reconstructs the exact 6-h sum)."""
    rows = []
    for s, vals in summaries.items():
        src = f"wn2-{s}"
        for v_iso, tot_in in zip(valid_utc, vals):
            end = dt.datetime.strptime(v_iso, "%Y-%m-%dT%H:%M:%SZ")
            mm_h = tot_in * IN_TO_MM / 6.0
            for h in range(6):
                valid = (end - dt.timedelta(hours=5 - h)
                         ).strftime("%Y-%m-%dT%H:00:00")
                rows.append((bid, issued_utc, valid, round(mm_h, 4), src))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    data = wn.latest()
    if data["status"] != "ok":
        # Cron-friendly: an unconfigured/unavailable source is a NORMAL state
        # until Google approves access — say so and exit clean, so the timer
        # unit doesn't page anyone for a dependency that isn't wired yet.
        print(f"weathernext ledger: skipped — {data['status']}")
        return 0

    issued = data["issued_utc"].replace("Z", "").split(".")[0]
    rows = []
    for bid, members in data["basins"].items():
        rows += hourly_rows(bid, issued, data["valid_utc"],
                            summarize(members))
    conn = ledger_db.connect(ledger_db.db_path(args.db))
    ledger_db.insert_forecasts(conn, rows)
    print(f"weathernext ledger: {len(rows)} atoms "
          f"({len(data['basins'])} basins x {len(SERIES)} series), "
          f"issued {issued} source {data['source']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
