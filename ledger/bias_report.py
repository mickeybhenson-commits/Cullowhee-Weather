#!/usr/bin/env python3
"""
bias_report.py — turn the QPF ledger into the published verification record.
============================================================================
This is the "climbing out of shadow mode" evidence: for every forecast
source in the ledger (om-best, nws-ndfd, wn2-mean, ...), how much does it
under- or over-call rain vs MRMS truth, per basin and per lead bucket?

    bias = SUM(qpf_mm) / SUM(qpe_mm)      over paired 6-h windows
    (ratio-of-sums, not mean-of-ratios: robust to the many near-zero
     windows that make per-window ratios explode)

  bias < 1.0  -> the source UNDER-calls (the orographic failure mode the
                 shadow-mode caveat documents; the number that seeds
                 WN_BIAS_MULT_JSON is 1/bias, upward-only, from wn2-mean)
  bias > 1.0  -> over-calls (costs false alarms, not misses)

FILTERS (match the analysis contract in schema_ledger.sql)
  * wet windows only: qpe_mm >= WET_MM (default 12.7 = 0.5 in / 6 h) —
    dry-window bias is meaningless and would swamp the signal
  * min_valid_frac >= 0.8 — MRMS windows with too many missing cells drop
  * a (source, basin, bucket) cell publishes only with >= MIN_N windows;
    below that the report says "insufficient" rather than printing a
    number that looks like evidence

OUTPUT
  JSON to --out (default feed/bias_report.json, rendered by bias.html),
  and a human table to stdout. The JSON always includes generated_utc and
  per-source window counts so a reader can see HOW MUCH evidence sits
  behind every number.

DEPLOYMENT
  Runs wherever the ledger DB lives (the qpf-* timers' host):
      python3 bias_report.py --db /var/lib/noah/qpf_ledger.db \
                             --out /opt/noah/feed/bias_report.json
  Publish by committing the JSON into the repo's feed/ dir (or rsync to the
  Pages host). bias.html degrades gracefully while no report exists.
  Run `python3 bias_report.py --selftest` for the offline self-test.
Standard library only.
"""

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger_db

WET_MM = 12.7          # 0.5 in per 6-h window: a hydrologically relevant rate
MIN_FRAC = 0.8
MIN_N = 8              # fewer paired wet windows than this -> "insufficient"
BUCKETS = [(0, 24, "0-24h"), (24, 48, "24-48h"), (48, 72, "48-72h"),
           (72, 240, "72h+")]

SQL = """
SELECT fc_source, basin_id, lead_hr, qpf_mm, qpe_mm
FROM pairs_6h
WHERE qpe_mm >= ? AND min_valid_frac >= ? AND lead_hr >= 0
"""


def bucket(lead_hr):
    """lo < lead <= hi: a window ENDING exactly at 24 h is day 1's last
    window, not day 2's first (lead is measured to the window END)."""
    for lo, hi, name in BUCKETS:
        if lo < lead_hr <= hi:
            return name
    return None


def build_report(conn, wet_mm=WET_MM, min_frac=MIN_FRAC, min_n=MIN_N):
    cells = {}          # (source, basin, bucket) -> [n, sum_qpf, sum_qpe]
    for src, bid, lead, qpf, qpe in conn.execute(SQL, (wet_mm, min_frac)):
        b = bucket(lead)
        if b is None:
            continue
        for key in ((src, bid, b), (src, "ALL", b), (src, "ALL", "ALL")):
            c = cells.setdefault(key, [0, 0.0, 0.0])
            c[0] += 1
            c[1] += qpf
            c[2] += qpe

    sources = {}
    for (src, bid, b), (n, sq, so) in sorted(cells.items()):
        cell = {"n": n}
        if n >= min_n and so > 0:
            cell["bias"] = round(sq / so, 3)
            cell["suggest_mult"] = round(max(1.0, so / sq), 2) if sq > 0 else None
        else:
            cell["bias"] = None
            cell["note"] = "insufficient"
        sources.setdefault(src, {}).setdefault(bid, {})[b] = cell
    return {
        "generated_utc": dt.datetime.now(dt.timezone.utc)
                           .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "filters": {"wet_mm": wet_mm, "min_valid_frac": min_frac,
                    "min_n": min_n},
        "buckets": [b[2] for b in BUCKETS] + ["ALL"],
        "note": ("bias = sum(QPF)/sum(MRMS QPE) over paired wet 6-h windows; "
                 "<1 under-calls. suggest_mult is the upward-only correction "
                 "(1/bias, floored at 1.0)."),
        "sources": sources,
    }


def print_table(rep):
    print(f"QPF bias report  ({rep['generated_utc']}; wet>= "
          f"{rep['filters']['wet_mm']} mm/6h, n>={rep['filters']['min_n']})")
    for src, basins in rep["sources"].items():
        overall = basins.get("ALL", {}).get("ALL", {})
        ov = overall.get("bias")
        print(f"\n  {src}:  overall bias "
              f"{ov if ov is not None else 'insufficient'}"
              f"  (n={overall.get('n', 0)})")
        for bid, buckets in basins.items():
            if bid == "ALL":
                continue
            cells = "  ".join(
                f"{b}={c['bias'] if c['bias'] is not None else '--'}(n{c['n']})"
                for b, c in buckets.items())
            print(f"    {bid:14s} {cells}")
    if not rep["sources"]:
        print("  (no paired wet windows yet — the ledger needs both forecast"
              " fetches AND MRMS observations over the same storm)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default="feed/bias_report.json")
    ap.add_argument("--wet-mm", type=float, default=WET_MM)
    args = ap.parse_args()
    conn = ledger_db.connect(ledger_db.db_path(args.db))
    rep = build_report(conn, wet_mm=args.wet_mm)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(rep, f, indent=2)
    print_table(rep)
    print(f"\nwritten: {args.out}")
    return 0


# ---------------------------------------------------------------------------
# self-test: synthetic ledger with a KNOWN planted bias, verify recovery
# ---------------------------------------------------------------------------
def _selftest():
    import tempfile
    db = tempfile.mktemp(suffix=".db")
    conn = ledger_db.connect(db)
    issued = "2026-08-01T00:00:00"
    fc_rows, obs_rows = [], []
    # 12 wet 6-h windows; forecast = 0.7 x truth (under-call bias 0.7)
    for w in range(12):
        for h in range(6):
            t = dt.datetime(2026, 8, 1, 0, 0) + dt.timedelta(hours=6 * w + h + 1)
            iso = t.strftime("%Y-%m-%dT%H:00:00")
            obs_rows.append(("CC-WCU-2260", iso, 3.0, 1.0, "mrms-p2"))     # 18 mm/6h
            fc_rows.append(("CC-WCU-2260", issued, iso, 2.1, "om-best"))   # 12.6
    # a dry day that must be filtered out entirely
    for h in range(24):
        t = dt.datetime(2026, 8, 5, 0, 0) + dt.timedelta(hours=h + 1)
        iso = t.strftime("%Y-%m-%dT%H:00:00")
        obs_rows.append(("CC-WCU-2260", iso, 0.1, 1.0, "mrms-p2"))
        fc_rows.append(("CC-WCU-2260", issued, iso, 5.0, "om-best"))
    # a second source with too few windows -> "insufficient"
    for h in range(6):
        t = dt.datetime(2026, 8, 1, 0, 0) + dt.timedelta(hours=h + 1)
        iso = t.strftime("%Y-%m-%dT%H:00:00")
        fc_rows.append(("CC-WCU-2260", issued, iso, 2.0, "nws-ndfd"))
    ledger_db.insert_forecasts(conn, fc_rows)
    ledger_db.insert_observations(conn, obs_rows)

    rep = build_report(conn)
    om = rep["sources"]["om-best"]["ALL"]["ALL"]
    assert om["n"] == 12, om
    assert abs(om["bias"] - 0.7) < 0.001, om
    assert abs(om["suggest_mult"] - round(1 / 0.7, 2)) < 0.01
    nws = rep["sources"]["nws-ndfd"]["ALL"]["ALL"]
    assert nws["bias"] is None and nws["note"] == "insufficient"
    # lead bucketing: 12 windows ending 6..72h after issuance -> 4 per bucket
    b = rep["sources"]["om-best"]["CC-WCU-2260"]
    assert b["0-24h"]["n"] == 4 and b["24-48h"]["n"] == 4 and b["48-72h"]["n"] == 4
    assert "72h+" not in b, b.get("72h+")
    print_table(rep)
    print("\nall bias_report self-tests passed (planted 0.7 bias recovered)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
