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
           "num_sensors", "qa", "trend", "query_ok", "note",
           # --- model comparison (site 25380 only) --------------------------
           "model_stage_ft",     # our engine's stage at this instant
           "obs_delta_ft",       # observed change since the last DISTINCT obs
           "model_delta_ft",     # our predicted change over that same span
           "delta_window_min",   # span between the two OBSERVATION stamps
           "model_window_min",   # span between the two RUN times
           "delta_resid_ft"]     # model_delta - obs_delta  (the error)
#
# Note on the two window columns. The observed delta spans FIMAN's observation
# timestamps; the model delta spans our run times, because we can only sample
# the model when we run. Because FIMAN batches, those windows often differ —
# an observation may jump 120 min while our two samples are 60 min apart.
# Differencing across mismatched windows is not a fair comparison, so BOTH are
# recorded and the analysis should keep only rows where they roughly agree
# (say within 20%). Silently comparing them would manufacture a skill number
# that does not mean anything.

# The only site our basin model actually predicts. The Tuckasegee control gage
# is a different drainage; logging a "prediction" against it would be noise.
MODEL_SITE = "25380"


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


def _model_stage() -> Optional[float]:
    """Our engine's current stage, or None. Imported lazily and defensively:
    the monitor's job is logging the state feed, and it must keep doing that
    even if our own model is broken or mid-edit.
    """
    try:
        from feed_runner import get_modeled_stage_ft
        v = get_modeled_stage_ft()
        return None if v is None else float(v)
    except Exception as e:                       # noqa: BLE001 - deliberate
        print(f"model stage unavailable: {type(e).__name__}: {e}")
        return None


def _prior_distinct(site: str, last_updated_utc: Optional[str]) -> Optional[dict]:
    """Most recent logged row for `site` whose observation timestamp DIFFERS
    from the current one.

    This is the whole trick. FIMAN batches roughly every two hours, so
    consecutive 30-minute runs usually see the SAME reading republished.
    Differencing against the previous ROW would yield a string of spurious
    0.00 ft deltas and make the model look perfect while telling us nothing.
    We difference against the last genuinely new observation instead.
    """
    if not OUT.exists() or not last_updated_utc:
        return None
    try:
        with OUT.open(newline="") as fh:
            rows = [r for r in csv.DictReader(fh)
                    if r.get("site_id") == site
                    and r.get("query_ok") == "1"
                    and r.get("last_updated_utc")
                    and r["last_updated_utc"] != last_updated_utc]
    except (OSError, csv.Error):
        return None
    return rows[-1] if rows else None


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "", "None") else float(v)
    except (TypeError, ValueError):
        return None


def add_model_comparison(r: dict) -> dict:
    """Attach our prediction and a datum-independent delta comparison.

    Deltas, not absolutes: FIMAN stage is measured above gage datum (2125.0,
    and disputed by NWS by 1.00 ft) while our model returns depth above
    streambed. Those zeros differ, so absolute values are not comparable —
    but a CHANGE of 0.6 ft is 0.6 ft in either reference. That lets rise-rate
    validation start now, before the survey resolves the datum.
    """
    for c in ("model_stage_ft", "obs_delta_ft", "model_delta_ft",
              "delta_window_min", "model_window_min", "delta_resid_ft"):
        r.setdefault(c, None)
    if r.get("site_id") != MODEL_SITE or not r.get("query_ok"):
        return r

    r["model_stage_ft"] = _model_stage()

    prev = _prior_distinct(MODEL_SITE, r.get("last_updated_utc"))
    if not prev:
        return r

    obs_now, obs_prev = _f(r.get("stage_ft")), _f(prev.get("stage_ft"))
    mod_now, mod_prev = _f(r.get("model_stage_ft")), _f(prev.get("model_stage_ft"))
    t_now = parse_fiman_ts(r.get("last_updated_raw"))
    t_prev = (datetime.fromisoformat(prev["last_updated_utc"])
              if prev.get("last_updated_utc") else None)

    if t_now and t_prev:
        r["delta_window_min"] = round((t_now - t_prev).total_seconds() / 60.0, 1)
    try:
        r["model_window_min"] = round(
            (datetime.fromisoformat(r["observed_utc"])
             - datetime.fromisoformat(prev["observed_utc"])
             ).total_seconds() / 60.0, 1)
    except (KeyError, TypeError, ValueError):
        pass
    if obs_now is not None and obs_prev is not None:
        r["obs_delta_ft"] = round(obs_now - obs_prev, 3)
    if mod_now is not None and mod_prev is not None:
        r["model_delta_ft"] = round(mod_now - mod_prev, 3)
    if r["obs_delta_ft"] is not None and r["model_delta_ft"] is not None:
        r["delta_resid_ft"] = round(r["model_delta_ft"] - r["obs_delta_ft"], 3)
    return r


def failure_row(now: datetime, site: str, note: str) -> dict:
    r = {c: None for c in COLUMNS}
    r.update({"observed_utc": now.isoformat(timespec="seconds"),
              "site_id": site, "query_ok": 0, "note": note[:300]})
    return r


def main() -> None:
    now = datetime.now(timezone.utc)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    try:
        rows = [add_model_comparison(row_for(a, now)) for a in query()]
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
            line = (f"{r['site_id']:>10}  age={r['age_min']} min  "
                    f"missed={r['intervals_missed']}  "
                    f"in_service={r['in_service']}  "
                    f"cond={r['condition_txt']}")
            if r.get("obs_delta_ft") is not None:
                line += (f"  |  obs{r['obs_delta_ft']:+.2f} "
                         f"model{r['model_delta_ft']:+.2f} "
                         f"resid{r['delta_resid_ft']:+.2f} ft "
                         f"over {r['delta_window_min']:.0f} min"
                         if r.get("delta_resid_ft") is not None
                         else f"  |  obs{r['obs_delta_ft']:+.2f} ft "
                              f"(no model value)")
            print(line)
        else:
            print(f"{r['site_id']:>10}  QUERY FAILED: {r['note']}")


if __name__ == "__main__":
    main()
