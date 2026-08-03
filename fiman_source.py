"""
fiman_source.py — gated MEASURED stage from FIMAN gage 25380 (CUCN7),
Cullowhee Creek at Speedwell: the state's only in-watershed asset.

This is the bridge from "we log that gage's reliability" (fiman_watch.py) to
"the confirmation tier can use it" — with the gates that make that honest:

  FRESHNESS   The gage reports on a 30-min service interval and has a
              documented habit of going silently stale while FIMAN still
              displays "Normal" (see fiman_watch.py header, 2026-07-28).
              A reading older than MAX_AGE_MIN (2.5 intervals) is NOT fresh
              and must not drive posture. `latest()` says so explicitly.

  DATUM       CULLOWHEE_DATUM_VERIFIED is still 0: the gage's stage datum
              has not been tied to a surveyed benchmark, so OUR absolute
              stage thresholds must not be applied to it. Classification
              therefore uses FIMAN'S OWN condition text (the state's
              calibrated-for-this-gage assessment), mapped to NOAH levels.
              Stage in feet is carried for display and rate-of-change only.

  CEILING     None here — a fresh "Minor Flooding" maps to WARNING and a
              fresh "Moderate/Major" to EMERGENCY, because this is a real
              measured in-watershed stage: exactly what the Confirmation
              tier exists for. (Forecast-tier signals stay capped at WATCH
              in flood_network; that design is unchanged.)

Consumers:
  latest()            -> one gated dict for the speedwell node (streamlit,
                         feed_runner's external.json)
  series_from_log()   -> [(epoch, stage_ft)] from the committed
                         feed/fiman_watch.csv — the repo IS the time series
  FimanStageBackend   -> sources.py backend (quantity Q_STAGE_GOV) for the
                         CC-SPD-1830 / speedwell basin ids
  install()           -> chain the backend in front of whatever is current

Standard library only, same as fiman_watch.py. Never raises out of latest().
"""

from __future__ import annotations

import csv
import io
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import os
from typing import Optional

import fiman_watch
import sources

SITE_ID = "25380"
NWS_LID = "CUCN7"
SOURCE_NAME = "NCEM FIMAN 25380 (CUCN7) — Cullowhee Ck @ Speedwell"

# 2.5 x the 30-min service interval. Beyond this the gage is silently stale
# (its documented failure mode) and the reading must not drive posture.
MAX_AGE_MIN = 75.0

# Basin/site ids this gage may speak for. The campus feed basin is
# DELIBERATELY absent: datum unverified + different location means this
# reading must never masquerade as a campus stage.
SERVES = {"speedwell", "CC-SPD-1830"}

# FIMAN condition text -> NOAH level. The state's own per-gage classification,
# which sidesteps our unverified datum entirely. Unknown text -> None (no
# level claimed; the reading is then display-only).
CONDITION_LEVEL = {
    "normal": "NORMAL",
    "monitor": "WATCH",
    "action": "WATCH",
    "minor flooding": "WARNING",
    "minor": "WARNING",
    "moderate flooding": "EMERGENCY",
    "moderate": "EMERGENCY",
    "major flooding": "EMERGENCY",
    "major": "EMERGENCY",
}

# ---------------------------------------------------------------------------
# GAGE DATUM — a NUMBER the survey produces, not a boolean someone flips.
#
# This used to be two separate flags: a hardcoded False here, and
# CULLOWHEE_DATUM_VERIFIED read from the environment in publish_feed.py. Two
# problems, both of which would have surfaced on the day the survey came back:
#
#   1. Flipping the env var changed publish_feed and left this module still
#      reporting datum_verified false, so anything consuming latest() would
#      disagree with the published feed.
#   2. Worse: publish_feed computed elevations as GAGE_DATUM + stage using the
#      hardcoded 2125.0 — the CONTESTED value. Setting the boolean would have
#      published every elevation off by whatever the survey actually found, and
#      labelled it VERIFIED. Strictly worse than today's suppression.
#
# A survey does not return "yes". It returns a number. So the number is the
# input, and "verified" is derived from having it. Set:
#
#     CULLOWHEE_GAGE_DATUM_NAVD88=2125.37     (whatever the crew measures)
#
# The site is contested between NCEM FIMAN 25380 (2125.0) and NWS CUCN7
# (implied 2126.0) — exactly 1.00 ft apart, with FIMAN's NAVD88_ELEVATION field
# unpopulated (0.0), which points at an NGVD29-vs-NAVD88 vintage mismatch.
# Until a surveyed number exists, every elevation stays suppressed.
DATUM_CONTESTED = {"NCEM FIMAN 25380": 2125.0, "NWS CUCN7 (implied)": 2126.0}


def gage_datum_navd88():
    """-> (datum_ft or None, verified: bool, note: str). Single source of truth.

    Raises if someone sets the legacy boolean without supplying the number —
    that is the one combination that would publish confident wrong elevations,
    so it fails loudly rather than guessing which contested value to use.
    """
    raw = os.getenv("CULLOWHEE_GAGE_DATUM_NAVD88", "").strip()
    legacy = os.getenv("CULLOWHEE_DATUM_VERIFIED", "0").strip() == "1"
    if raw:
        try:
            return float(raw), True, f"surveyed gage datum {float(raw):.2f} ft NAVD88"
        except ValueError:
            raise ValueError(
                f"CULLOWHEE_GAGE_DATUM_NAVD88={raw!r} is not a number. It must be "
                "the surveyed gage-zero elevation in ft NAVD88.")
    if legacy:
        raise ValueError(
            "CULLOWHEE_DATUM_VERIFIED=1 but CULLOWHEE_GAGE_DATUM_NAVD88 is unset. "
            "Refusing to publish elevations: the datum is contested between "
            + " and ".join(f"{k} {v}" for k, v in DATUM_CONTESTED.items())
            + ", and picking one would publish a foot-wrong elevation labelled "
              "VERIFIED. Set the surveyed number instead.")
    return None, False, (
        "gage datum unresolved ("
        + " vs ".join(f"{k} {v}" for k, v in DATUM_CONTESTED.items())
        + "); elevations suppressed, stage and condition only")


GAGE_DATUM_FT, DATUM_VERIFIED, DATUM_NOTE = gage_datum_navd88()
RAW_LOG_URL = ("https://raw.githubusercontent.com/mickeybhenson-commits/"
               "Cullowhee-Weather/main/feed/fiman_watch.csv")


def level_from_condition(txt: Optional[str]) -> Optional[str]:
    if not txt:
        return None
    return CONDITION_LEVEL.get(str(txt).strip().lower())


def latest(now: Optional[datetime] = None) -> Optional[dict]:
    """Current gated reading, or None if FIMAN is unreachable.

    Returns dict with: stage_ft, level (None if condition unmapped),
    condition, age_min, fresh (bool — ONLY fresh readings may drive posture),
    last_updated_utc, trend, rain_1hr, rain_24hr, datum_verified, source.
    """
    now = now or datetime.now(timezone.utc)
    try:
        rows = fiman_watch.query()
    except Exception:
        return None
    attrs = None
    for a in rows:
        if str(a.get("SITE_ID")) == SITE_ID:
            attrs = a
            break
    if attrs is None:
        return None
    r = fiman_watch.row_for(attrs, now)
    age = r.get("age_min")
    fresh = (r.get("query_ok") == 1 and r.get("stage_ft") is not None
             and age is not None and 0 <= age <= MAX_AGE_MIN)
    return {
        "site_id": SITE_ID, "nws_lid": NWS_LID, "source": SOURCE_NAME,
        "stage_ft": r.get("stage_ft"),
        "condition": r.get("condition_txt"),
        "level": level_from_condition(r.get("condition_txt")),
        "trend": r.get("trend"),
        "rain_1hr": r.get("rain_1hr"), "rain_24hr": r.get("rain_24hr"),
        "age_min": age, "fresh": fresh,
        "last_updated_utc": r.get("last_updated_utc"),
        "datum_verified": DATUM_VERIFIED,
        "gage_datum_ft": GAGE_DATUM_FT,
        # NAVD88 water-surface elevation, and only once a surveyed datum
        # exists. None until then - that is the whole point of the gate.
        "wse_navd88_ft": (None if (GAGE_DATUM_FT is None or r.get("stage_ft") is None)
                          else round(GAGE_DATUM_FT + r["stage_ft"], 2)),
        "datum_note": DATUM_NOTE,
        "note": ("" if fresh else
                 f"NOT driving posture: stale/invalid (age {age} min, "
                 f"limit {MAX_AGE_MIN:.0f})"),
    }


def series_from_log(path: str = "feed/fiman_watch.csv",
                    fetch_if_missing: bool = True) -> list:
    """[(epoch, stage_ft)] for gage 25380 from the committed watch log —
    30-min cadence, de-duplicated on the gage's own observation stamp.
    Reads the local file when running inside the repo; falls back to the
    raw.githubusercontent copy (e.g. Streamlit Cloud without the feed dir)."""
    text = None
    p = Path(path)
    if p.exists():
        text = p.read_text(errors="replace")
    elif fetch_if_missing:
        try:
            req = urllib.request.Request(
                RAW_LOG_URL, headers={"User-Agent": "Cullowhee-Weather NOAH"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode(errors="replace")
        except Exception:
            return []
    if not text:
        return []
    out, seen = [], set()
    try:
        for row in csv.DictReader(io.StringIO(text)):
            if str(row.get("site_id")) != SITE_ID or row.get("query_ok") != "1":
                continue
            ts_raw, st_raw = row.get("last_updated_utc"), row.get("stage_ft")
            if not ts_raw or not st_raw or ts_raw in seen:
                continue
            try:
                t = int(datetime.fromisoformat(ts_raw).timestamp())
                out.append((t, float(st_raw)))
                seen.add(ts_raw)
            except (ValueError, TypeError):
                continue
    except csv.Error:
        return []
    out.sort()
    return out


# ---------------------------------------------------------------------------
# sources.py backend  (quantity Q_STAGE_GOV — 75-min freshness, vs the 12-min
# gate on Q_STAGE which is tuned for NOAH's own future 5-min LoRa telemetry)
# ---------------------------------------------------------------------------
class FimanStageBackend(sources.SensorBackend):
    """Serves Q_STAGE_GOV for the Speedwell node only. Returns None (falls
    through the chain) when the gage is stale, unmapped, or unreachable."""

    def latest(self, quantity: str, basin_id: str):
        if quantity != sources.Q_STAGE_GOV or basin_id not in SERVES:
            return None
        r = latest()
        if not r or not r["fresh"]:
            return None
        ts = None
        if r["last_updated_utc"]:
            try:
                ts = datetime.fromisoformat(r["last_updated_utc"])
            except ValueError:
                ts = None
        return sources.Reading(
            value=float(r["stage_ft"]), tier=sources.MEASURED,
            source=SOURCE_NAME, ts=ts, quantity=quantity,
            note="datum unverified; level via FIMAN condition")


def install() -> None:
    """Chain the FIMAN backend in front of whatever backend is current."""
    current = sources.current_backend()
    sources.set_backend(sources.ChainBackend([FimanStageBackend(), current]))


if __name__ == "__main__":
    r = latest()
    print(r if r else "FIMAN unreachable")
    s = series_from_log()
    print(f"log series: {len(s)} distinct observations"
          + (f", last {s[-1]}" if s else ""))
