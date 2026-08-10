#!/usr/bin/env python3
"""
fetch_nws_qpf.py — archive NWS gridded QPF into the verification ledger.
========================================================================
Third forecast source in the ledger, beside om-best (Open-Meteo) and the
wn2-* WeatherNext summaries. Source tag 'nws-ndfd'.

WHY THIS ONE MATTERS FOR THE BIAS QUESTION
  The GSP forecast grids are 2.5 km with a human forecaster who KNOWS the
  escarpment adjusts them. If any operational product under-calls Cullowhee
  orographic rain the least, it is plausibly this one. A season of
  (nws-ndfd vs MRMS) beside (om-best vs MRMS) answers whether the system's
  operational QPF input should switch — with evidence, not vibes.

Atoms are hourly (NWS durations split uniformly — see nws_qpf.py header;
6-h window sums are exact, single hours are an artifact). Issuance is
stamped at the fetch hour: the NWS grid updates continuously, so unlike a
synoptic model there is no discrete init time; lead-time buckets derived
from (valid - fetched) are therefore approximate by up to an hour, which
is what the existing om-best rows already accept.

Run: same cadence as qpf-forecast (deploy/qpf-nws.timer), or by hand:
    python3 fetch_nws_qpf.py [--db /path/to/qpf_ledger.db]
Deps: standard library only.
"""

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))            # ledger/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ledger_db
import nws_qpf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    issued = dt.datetime.now(dt.timezone.utc).replace(
        minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00:00")
    try:
        atoms_by_bid, ncells = nws_qpf.fetch_atoms()
    except Exception as e:                            # noqa: BLE001
        # cron-friendly: a fetch failure is logged, not fatal — the next
        # cycle catches up, and fc gaps are visible in the ledger itself.
        print(f"nws ledger: fetch failed ({type(e).__name__}: {e})")
        return 0

    rows = [(bid, issued, end_iso, mm, nws_qpf.SOURCE)
            for bid, atoms in atoms_by_bid.items()
            for end_iso, mm in atoms]
    if not rows:
        print("nws ledger: no atoms parsed (empty QPF grids?)")
        return 0
    conn = ledger_db.connect(ledger_db.db_path(args.db))
    ledger_db.insert_forecasts(conn, rows)
    print(f"nws ledger: {len(rows)} atoms from {ncells} grid cells, "
          f"issued {issued}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
