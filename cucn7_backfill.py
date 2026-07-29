"""
cucn7_backfill.py — retrieve the archived telemetry record for the Cullowhee
Creek gage (NWS id CUCN7) from the Iowa Environmental Mesonet's HADS/SHEF
archive, one day at a time.

Why IEM: the FIMAN MapServer serves only a current snapshot, and the NWS API
holds zero observed data for this gage. IEM has quietly archived the raw
telemetry stream. A probe of 2013-01-17 returned ~50 observations at 15-30
minute cadence whose stage peak (3.26 ft) matches the official crest record
exactly, plus a cumulative precipitation counter from the gage's own rain
sensor — same-site rain and stage, which is the cleanest possible pairing for
event correlation. A probe of 2009-09-26 (the flood of record) returned
nothing, so the archive has boundaries; mapping them is part of the point.

Outputs (all in feed/):
  cucn7_history.csv         long format: utc_valid, var, value
                            (vars are SHEF codes: HGIRRZ = stage ft,
                             PCIRRZ = cumulative precip in, PP* = precip
                             increments; long format because the exact
                             variable set may drift across the years)
  cucn7_extent.csv          one row per day: date, n_obs, n_stage,
                            stage_min, stage_max, pc_min, pc_max
                            — this is the coverage-timeline figure's data
  cucn7_backfill_state.json resume checkpoint

Behavior:
  * Resumable. State records the last completed day; re-running continues
    from the next day. Safe to run repeatedly.
  * Polite. One request per day of record, throttled, with backoff, and a
    descriptive User-Agent. IEM provides this archive as a courtesy;
    treat it that way.
  * Time-boxed. Exits cleanly before the Actions job limit with a PARTIAL
    banner; run the workflow again to continue. Prints BACKFILL COMPLETE
    when it reaches the end date.

Env overrides: BACKFILL_START, BACKFILL_END (ISO dates),
               BACKFILL_MAX_MINUTES (default 320).
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

STATION, NETWORK = "CUCN7", "NC_DCP"
API = "https://mesonet.agron.iastate.edu/api/1/obhistory.json"

OUT = Path("feed")
HIST = OUT / "cucn7_history.csv"
EXTENT = OUT / "cucn7_extent.csv"
STATE = OUT / "cucn7_backfill_state.json"

# HADS archive generally begins ~2002; earlier days return empty quickly and
# an empty day is itself extent-map information, so start wide.
START = date.fromisoformat(os.getenv("BACKFILL_START", "2002-01-01"))
END = (date.fromisoformat(os.getenv("BACKFILL_END"))
       if os.getenv("BACKFILL_END")
       else datetime.now(timezone.utc).date() - timedelta(days=1))
MAX_MINUTES = float(os.getenv("BACKFILL_MAX_MINUTES", "320"))
SLEEP_S = 0.4          # between requests
FAIL_LIMIT = 8         # consecutive failures before giving up this run

# SHEF physical-element prefixes worth keeping (stage, precip, and a few
# extras that cost nothing). Keys must look like SHEF codes, e.g. HGIRRZ.
SHEF_KEY = re.compile(r"^(HG|HP|PC|PP|TA|TW|VB|XR|US|UD)[A-Z0-9]{2,5}$")


def parse_rows(rows: list) -> list[tuple[str, str, float]]:
    """Extract (utc, var, value) triples.

    Defensive about shape: the API has been observed to present observations
    as wide rows (one dict per timestamp with SHEF-coded columns) and its
    schema also describes a key/value form. Accept both.
    """
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        utc = row.get("utc_valid") or row.get("valid") or row.get("utc")
        if not utc:
            continue
        if "key" in row and "value" in row:
            k, v = str(row.get("key", "")).upper(), row.get("value")
            if v is not None and SHEF_KEY.match(k):
                try:
                    out.append((utc, k, float(v)))
                except (TypeError, ValueError):
                    pass
            continue
        for k, v in row.items():
            if v is None:
                continue
            ku = str(k).upper()
            if SHEF_KEY.match(ku):
                try:
                    out.append((utc, ku, float(v)))
                except (TypeError, ValueError):
                    pass
    return out


def fetch_day(d: date) -> Optional[list[tuple[str, str, float]]]:
    """One day's triples, or None after exhausting retries."""
    url = API + "?" + urllib.parse.urlencode({
        "network": NETWORK, "station": STATION,
        "date": d.isoformat(), "full": "1"})
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Cullowhee-Weather archive backfill "
                       "(hydrology research, Jackson County NC; "
                       "github.com/mickeybhenson-commits/Cullowhee-Weather)")})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            return parse_rows(data.get("data") or [])
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            if attempt == 3:
                return None
            time.sleep(3 * (attempt + 1))
    return None


def day_summary(d: date, triples: list) -> dict:
    stamps = {t for t, _, _ in triples}
    stages = [v for _, k, v in triples if k.startswith("HG")]
    pcs = [v for _, k, v in triples if k.startswith("PC")]
    return {
        "date": d.isoformat(),
        "n_obs": len(stamps),
        "n_stage": len(stages),
        "stage_min": round(min(stages), 2) if stages else None,
        "stage_max": round(max(stages), 2) if stages else None,
        "pc_min": round(min(pcs), 2) if pcs else None,
        "pc_max": round(max(pcs), 2) if pcs else None,
    }


def _ensure_headers() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if not HIST.exists():
        HIST.write_text("utc_valid,var,value\n")
    if not EXTENT.exists():
        EXTENT.write_text(
            "date,n_obs,n_stage,stage_min,stage_max,pc_min,pc_max\n")


def main() -> None:
    _ensure_headers()
    try:
        state = json.loads(STATE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    resume = (date.fromisoformat(state["completed_through"])
              + timedelta(days=1)) if state.get("completed_through") else START

    if resume > END:
        print(f"BACKFILL COMPLETE — already covered through {END}")
        return

    t0 = time.monotonic()
    d, fails, days_done, days_with_data = resume, 0, 0, 0
    hist_fh = HIST.open("a", newline="")
    extent_fh = EXTENT.open("a", newline="")
    hist_w, extent_w = csv.writer(hist_fh), csv.writer(extent_fh)

    print(f"backfilling {STATION} from {resume} to {END}")
    while d <= END:
        if (time.monotonic() - t0) / 60.0 > MAX_MINUTES:
            print(f"PARTIAL — time budget reached at {d}. "
                  f"Run the workflow again to continue.")
            break
        triples = fetch_day(d)
        if triples is None:
            fails += 1
            print(f"{d}: fetch failed ({fails}/{FAIL_LIMIT})")
            if fails >= FAIL_LIMIT:
                print(f"PARTIAL — {FAIL_LIMIT} consecutive failures; "
                      f"stopping politely at {d}. Run again later.")
                break
            time.sleep(30)
            continue
        fails = 0
        for utc, k, v in triples:
            hist_w.writerow([utc, k, v])
        s = day_summary(d, triples)
        extent_w.writerow([s[c] for c in
                           ("date", "n_obs", "n_stage", "stage_min",
                            "stage_max", "pc_min", "pc_max")])
        if triples:
            days_with_data += 1
        days_done += 1
        state["completed_through"] = d.isoformat()
        if days_done % 25 == 0:
            hist_fh.flush(); extent_fh.flush()
            STATE.write_text(json.dumps(state))
            print(f"...{d}  ({days_done} days this run, "
                  f"{days_with_data} with data)")
        d += timedelta(days=1)
        time.sleep(SLEEP_S)
    else:
        print(f"BACKFILL COMPLETE through {END} — "
              f"{days_done} days this run, {days_with_data} with data.")

    hist_fh.close(); extent_fh.close()
    STATE.write_text(json.dumps(state))


if __name__ == "__main__":
    main()
