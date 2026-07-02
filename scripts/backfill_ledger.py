#!/usr/bin/env python3
"""
backfill_ledger.py — ONE-SHOT (workstation): seed the QPF-bias ledger back
to January 2024 so it starts life with ~2.5 years of pairs, including Helene.
=============================================================================
Two phases, runnable independently:

  --forecasts   Open-Meteo Previous Runs API. Provides precipitation at fixed
                lead-time offsets (previous_day1..7 = value forecast 24..168 h
                before valid time; day0 = the near-realtime stitched series).
                Coverage starts January 2024 — that is the hard floor for
                lead-stratified backfill; the pre-2024 archive exists only as
                the ~day-0 Historical Forecast series, which carries no lead
                information and is skipped here.
                issued_utc is stored as (valid - 24h*N): an APPROXIMATION of
                issuance, exact only in the derived lead bucket. Rows carry
                source='om-prev-runs' so analysis can treat them separately
                from live 'om-best' rows.

  --mrms        Hourly MultiSensor_QPE_01H_Pass2 from mtarchive, reusing the
                fetch_mrms machinery (eccodes decoder — pip install eccodes).
                SIZE WARNING: ~22,000 hourly CONUS files
                at a few MB each — tens of GB of transfer (files are processed
                and discarded immediately; disk use stays small). Run it on a
                workstation connection, expect several hours to a day, and
                use --start/--end to chunk. Interruptible: already-ingested
                hours are skipped on restart.

HONEST CAVEATS TO CARRY INTO THE PAPER (also in README):
  * The archived blend spans model upgrades — bias is non-stationary across
    the backfill period.
  * MRMS truth is weakest during WNC extremes (beam blockage, gauge failures
    during Helene) — cross-check major events against Stage IV / post-event
    gauge analyses before trusting the bias sample they contribute.

RUN (from the repo root):
    python3 scripts/backfill_ledger.py --db ./qpf_ledger.db --forecasts
    python3 scripts/backfill_ledger.py --db ./qpf_ledger.db --mrms
then copy the .db to the VM (see README_ledger.md).

Deps: standard library; --mrms additionally needs eccodes (pip).
"""

import argparse
import calendar
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

# import the ledger package from the repo without installing it
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "ledger"))
import ledger_db                     # noqa: E402
import fetch_mrms                    # noqa: E402
from fetch_forecast import BASIN_POINTS  # noqa: E402

PREV_API = "https://previous-runs-api.open-meteo.com/v1/forecast"
DEFAULT_START = "2024-01-01"         # Previous Runs archive floor
LEAD_DAYS = range(0, 8)              # day0 (near-realtime) .. day7
FC_SOURCE = "om-prev-runs"
SLEEP_S = 1.0                        # be polite to the free tier


def month_chunks(start, end):
    """Yield (first_day, last_day) date pairs, calendar-month chunks."""
    cur = start.replace(day=1)
    while cur <= end:
        last = cur.replace(day=calendar.monthrange(cur.year, cur.month)[1])
        yield max(cur, start), min(last, end)
        cur = (last + dt.timedelta(days=1))


def backfill_forecasts(conn, start, end):
    hourly_vars = ["precipitation"] + [
        f"precipitation_previous_day{n}" for n in range(1, 8)]
    total = 0
    for bid, (lat, lon) in BASIN_POINTS.items():
        for d0, d1 in month_chunks(start, end):
            qs = urllib.parse.urlencode({
                "latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}",
                "hourly": ",".join(hourly_vars),
                "start_date": d0.isoformat(), "end_date": d1.isoformat(),
                "timezone": "UTC",
            })
            req = urllib.request.Request(
                PREV_API + "?" + qs,
                headers={"User-Agent": "WCU-NOAH-qpf-ledger/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.load(r)
            except Exception as e:                    # noqa: BLE001
                print(f"  {bid} {d0:%Y-%m}: FAILED ({e}) — rerun to fill",
                      file=sys.stderr)
                time.sleep(SLEEP_S)
                continue

            hh = data.get("hourly", {})
            times = hh.get("time", [])
            rows = []
            for n in LEAD_DAYS:
                key = ("precipitation" if n == 0
                       else f"precipitation_previous_day{n}")
                series = hh.get(key)
                if not series:
                    continue
                for t, v in zip(times, series):
                    if v is None:
                        continue
                    valid = dt.datetime.fromisoformat(t)
                    issued = (valid - dt.timedelta(days=n)).strftime(
                        "%Y-%m-%dT%H:00:00")
                    rows.append((bid, issued, valid.strftime(
                        "%Y-%m-%dT%H:00:00"), float(v), FC_SOURCE))
            ledger_db.insert_forecasts(conn, rows)
            total += len(rows)
            print(f"  {bid} {d0:%Y-%m}: {len(rows)} atoms")
            time.sleep(SLEEP_S)
    print(f"forecast backfill complete: {total} atoms")


def backfill_mrms(conn, start, end):
    basins, _ = fetch_mrms.load_masks()
    when = dt.datetime.combine(start, dt.time(1), tzinfo=dt.timezone.utc)
    stop = dt.datetime.combine(end + dt.timedelta(days=1), dt.time(0),
                               tzinfo=dt.timezone.utc)
    n_hours = int((stop - when).total_seconds() // 3600) + 1
    print(f"MRMS backfill: {n_hours} hours "
          f"({when:%Y-%m-%dT%H}Z .. {stop:%Y-%m-%dT%H}Z)")
    done = missed = 0
    while when <= stop:
        valid = when.strftime("%Y-%m-%dT%H:00:00")
        if not ledger_db.have_observation(conn, valid, fetch_mrms.SOURCE):
            ok = fetch_mrms.process_hour(conn, when, basins, quiet=True)
            done += ok
            missed += (not ok)
            if (done + missed) % 500 == 0:
                print(f"  ...{done} ingested, {missed} unavailable, "
                      f"at {valid}Z")
            time.sleep(0.2)
        when += dt.timedelta(hours=1)
    print(f"MRMS backfill complete: {done} ingested, {missed} unavailable "
          f"(rerun later; already-ingested hours are skipped)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="./qpf_ledger.db",
                    help="seed DB path (copy to the VM afterwards)")
    ap.add_argument("--forecasts", action="store_true")
    ap.add_argument("--mrms", action="store_true")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end",
                    default=dt.date.today().isoformat())
    args = ap.parse_args()
    if not (args.forecasts or args.mrms):
        ap.error("nothing to do: pass --forecasts and/or --mrms")

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    if start < dt.date(2024, 1, 1) and args.forecasts:
        print("NOTE: Previous Runs precipitation archive starts 2024-01-01; "
              "clamping forecast backfill.", file=sys.stderr)
        start = max(start, dt.date(2024, 1, 1))

    conn = ledger_db.connect(args.db)
    if args.forecasts:
        backfill_forecasts(conn, start, end)
    if args.mrms:
        if not fetch_mrms.decoder_available():
            sys.exit("eccodes not importable — pip install eccodes")
        backfill_mrms(conn, dt.date.fromisoformat(args.start), end)
    conn.close()


if __name__ == "__main__":
    main()
