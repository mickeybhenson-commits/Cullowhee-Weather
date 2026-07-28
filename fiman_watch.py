"""
fiman_watch.py — log how often the state's in-watershed gage actually reports.

Purpose
-------
NCEM FIMAN publishes gage 25380 (Cullowhee Creek) as IN_SERVICE=1 with
CONDITION_TXT="Normal" regardless of how long ago it last reported. On
2026-07-28 its LAST_UPDATED sat at 4:33 PM EDT for at least 2h38m — five
consecutive missed intervals on a 0.5 hr service interval — while still
displaying as Normal.

A single evening's observation is an anecdote. This script turns it into a
record: every run appends one row per watched gage to feed/fiman_watch.csv,
committed to git, so the reporting history is timestamped by the commit log
rather than assembled after the fact.

Deliberately logs FAILURES as rows too. "FIMAN was unreachable" is data about
the reliability of the feed, not an error to be swallowed.

Standard library only. Safe to run alongside feed_runner.py; it never raises,
so it cannot take down the publish job.
"""

from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

SERVICE = ("https://spartagis.ncem.org/arcgis/rest/services"
           "/FIMAN/GAGES_ALL/MapServer/0/query")

# Gages to watch. 25380 is the subject: the only state asset inside the
# Cullowhee Creek watershed. 03508050 is a control — a USGS-owned gage on the
# neighbouring Tuckasegee. Watching both distinguishes "this gage is down"
# from "the whole FIMAN feed is down", which are very different claims.
WATCH_SITES = ["25380", "03508050"]

# FIMAN's LAST_UPDATED is a naive local string with no zone. NCEM operates in
# Eastern. If that assumption is ever shown wrong the ages here shift by a
# fixed offset, so the *pattern* of gaps stays valid either way.
FIMAN_TZ = ZoneInfo("America/New_York")

OUT = Path("feed/fiman_watch.csv")

FIELDS = ["SITE_ID", "NAME", "LAST_UPDATED", "IN_SERVICE", "CONDITION_TXT",
          "HYDRO_ALL_STAGE", "CURRENT_ELEVATION_MSL", "RAIN_1HR", "RAIN_24HR",
          "SRV_INT", "NUM_SENSORS", "QA", "TREND"]

COLUMNS = ["observed_utc", "site_id", "name", "last_updated_raw",
           "last_updated_utc", "age_min", "intervals_missed", "in_service",
           "condition_txt", "stage_ft", "rain_1hr", "rain_24hr", "srv_int",
           "num_sensors", "qa", "trend", "query_ok", "note"]


def parse_fiman_ts(raw: Optional[str]) -> Optional[datetime]:
    """Parse 'Jul 28 2026  4:33PM' (note the doubled space, no space before
    the meridiem) into an aware UTC datetime. Returns None if unparseable —
    an unparseable timestamp is itself worth recording rather than crashing.
    """
    if not raw:
        return None
    s = re.sub(r"\s+", " ", str(raw).strip())
    s = re.sub(r"(?i)\s*([AP]M)$", r" \1", s)   # "4:33PM" -> "4:33 PM"
    for fmt in ("%b %d %Y %I:%M %p", "%b %d %Y %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return (datetime.strptime(s, fmt)
                    .replace(tzinfo=FIMAN_TZ)
                    .astimezone(timezone.utc))
        except ValueError:
            continue
    return None


def query() -> list[dict]:
    where = "SITE_ID IN ({})".format(
        ",".join("'%s'" % s for s in WATCH_SITES))
    url = SERVICE + "?" + urllib.parse.urlencode({
        "where": where,
        "outFields": ",".join(FIELDS),
        "returnGeometry": "false",
        "f": "json",
    })
    req = urllib.request.Request(url, headers={
        "User-Agent": "Cullowhee-Weather feed monitor (Jackson County NC)"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.load(resp)
    if "error" in data:
        raise RuntimeError(str(data["error"])[:200])
    return [f.get("attributes", {}) for f in data.get("features", [])]


def row_for(a: dict, now: datetime) -> dict:
    raw = a.get("LAST_UPDATED")
    ts = parse_fiman_ts(raw)
    age = None if ts is None else round((now - ts).total_seconds() / 60.0, 1)
    srv = a.get("SRV_INT")
    missed = None
    if age is not None and srv:
        try:
            missed = int(age // (float(srv) * 60.0))
        except (TypeError, ValueError, ZeroDivisionError):
            missed = None
    return {
        "observed_utc": now.isoformat(timespec="seconds"),
        "site_id": a.get("SITE_ID"),
        "name": a.get("NAME"),
        "last_updated_raw": raw,
        "last_updated_utc": None if ts is None else ts.isoformat(timespec="seconds"),
        "age_min": age,
        "intervals_missed": missed,
        "in_service": a.get("IN_SERVICE"),
        "condition_txt": a.get("CONDITION_TXT"),
        "stage_ft": a.get("HYDRO_ALL_STAGE"),
        "rain_1hr": a.get("RAIN_1HR"),
        "rain_24hr": a.get("RAIN_24HR"),
        "srv_int": srv,
        "num_sensors": a.get("NUM_SENSORS"),
        "qa": (a.get("QA") or "").strip(),
        "trend": a.get("TREND"),
        "query_ok": 1,
        "note": "",
    }


def failure_row(now: datetime, site: str, note: str) -> dict:
    r = {c: None for c in COLUMNS}
    r.update({"observed_utc": now.isoformat(timespec="seconds"),
              "site_id": site, "query_ok": 0, "note": note[:300]})
    return r


def main() -> None:
    now = datetime.now(timezone.utc)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    try:
        rows = [row_for(a, now) for a in query()]
        seen = {r["site_id"] for r in rows}
        # A site we asked for but did not get back is itself a finding.
        for s in WATCH_SITES:
            if s not in seen:
                rows.append(failure_row(now, s, "site absent from response"))
    except (urllib.error.URLError, TimeoutError, ValueError,
            RuntimeError, OSError) as e:
        rows = [failure_row(now, s, f"{type(e).__name__}: {e}")
                for s in WATCH_SITES]

    new = not OUT.exists()
    with OUT.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerows(rows)

    for r in rows:
        if r["query_ok"]:
            print(f"{r['site_id']:>10}  age={r['age_min']} min  "
                  f"missed={r['intervals_missed']}  "
                  f"in_service={r['in_service']}  "
                  f"cond={r['condition_txt']}")
        else:
            print(f"{r['site_id']:>10}  QUERY FAILED: {r['note']}")


if __name__ == "__main__":
    main()
