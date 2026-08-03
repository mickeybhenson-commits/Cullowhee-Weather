"""
publish_feed.py — emit a FIMAN-schema-compatible GeoJSON feed from the
Cullowhee-Weather flood engine, so Jackson County EM can consume it in the
same Esri client they already use for the state's FIMAN layer.

Design intent
-------------
JCEM already receives Cullowhee Creek *observations* from the state
(NCEM gage SITE_ID 25380, AHPS id cucn7, 30-min interval). This feed is
NOT a replacement for that. It carries what FIMAN does not have:
rate-of-rise, time-to-threshold, TR-55 modeled peak, sub-basin resolution,
and an early-warning probability.

So: mirror FIMAN's field names for the observation columns (so the layer
drops into an existing map with no re-symbolization), and namespace every
modeled/forecast column with `FX_` so nobody can mistake a model output
for a measurement.

Output
------
  docs/gages.geojson   — the feed (publish via GitHub Pages)
  docs/feed_meta.json  — freshness + provenance, for the consumer to check

Both are static files. No server required. GitHub Actions on a cron is
sufficient; see the accompanying workflow.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------
# Site registry. GAGE_DATUM is the elevation (ft, NAVD88) of stage = 0.00.
# It is the single most dangerous number in this file: every MSL value
# published downstream is derived from it. Do not estimate it. The NCEM
# value for Cullowhee Creek (site 25380) is 2125.0 and is reproduced here;
# any *new* sensor location needs a survey before it publishes MSL at all.
# --------------------------------------------------------------------------

# !! The gage datum for this site is CONTESTED between two official sources:
#
#   NCEM FIMAN (site 25380):  GAGE_DATUM 2125.0  -> bankfull(4.0 ft) = 2129.0
#   NWS NWPS   (CUCN7)     :  implied     2126.0 -> bankfull(4.0 ft) = 2130.0
#                                                   moderate(9.0 ft) = 2135.0
#
# The two agree exactly on STAGE categories (4 / 5 / 9 / 15 ft) and disagree by
# exactly 1.00 ft on ELEVATION. FIMAN's NAVD88_ELEVATION for the site is 0.0
# (unpopulated), which points at an NGVD29-vs-NAVD88 vintage mismatch.
#
# Until the survey resolves it this publisher emits STAGE ONLY — every MSL
# field goes out null. Publishing an elevation that is a foot wrong is worse
# than publishing no elevation at all.
#
# The gate is a NUMBER, not a boolean, and it lives in fiman_source so this
# module and that one cannot disagree. Previously DATUM_VERIFIED was read here
# from CULLOWHEE_DATUM_VERIFIED while fiman_source hardcoded its own False -
# and, worse, flipping that boolean would have published GAGE_DATUM + stage
# using the CONTESTED 2125.0 below, labelled VERIFIED. Setting the surveyed
# number is now the only way to turn elevations on, and the number that gets
# used is the one the crew measured.
from fiman_source import gage_datum_navd88

SURVEYED_DATUM, DATUM_VERIFIED, DATUM_NOTE = gage_datum_navd88()

SITES = {
    "CULLOWHEE_CK": {
        "SITE_ID": "JCEM-CULCK-01",
        "NAME": "Cullowhee Creek (Jackson Co. model)",
        "COUNTY": "JACKSON",
        "OWNER": "Jackson County / Cullowhee-Weather",
        "LATITUDE": 35.2887,
        "LONGITUDE": -83.1815,
        "GAGE_DATUM": 2125.0,      # CONTESTED — see note above
        "DATUM_SOURCE": "NCEM FIMAN site 25380; disputed by NWS CUCN7 by 1.00 ft",
        "River_Basin": "Little Tennessee",
        "WFO": "GSP",
        "SRV_INT": 0.5,            # publish interval, hours
        "REFERENCE_GAGE": "25380",
        "AHPS_ID": "cucn7",
    },
}

# Local warning levels, in STAGE FEET.
#
# These are a Jackson County product derived from the basin model. They are
# NOT NWS flood categories. The state's categories for the co-located NCEM
# gage convert to stage as: bankfull 4.0 / minor 5.0 / moderate 9.0 /
# major 15.0. Note WARNING coincides with the state's MODERATE, but
# EMERGENCY (11.0) fires four feet below the state's MAJOR (15.0).
# That divergence is intentional and must be disclosed to the consumer,
# not smoothed over — see FX_LEVEL_BASIS below.
LOCAL_LEVELS = {"WATCH": 7.0, "WARNING": 9.0, "EMERGENCY": 11.0}

# State categories in stage feet, for side-by-side publication.
STATE_LEVELS_STAGE = {"BANK_FULL": 4.0, "MINOR": 5.0, "MODERATE": 9.0, "MAJOR": 15.0}

# Freshness. Past STALE_MIN a reading is republished but flagged; past
# DEAD_MIN the feed publishes CONDITION_TXT="No Data" rather than a value.
# A dead sensor must never leave a plausible number sitting on the map.
STALE_MIN = 30
DEAD_MIN = 180

SHADOW_MODE = os.getenv("CULLOWHEE_SHADOW_MODE", "1") == "1"


@dataclass
class Reading:
    stage_ft: float
    observed_utc: datetime
    trend: str                      # "Rising" | "Falling" | "Constant"
    source: str                     # "SENSOR" | "MODEL" | "GOV"
    # Level as computed by flood_engine.classify_stage WITH hysteresis carried
    # across runs. When present this wins over the stateless recomputation
    # below, because the deadband is the whole point.
    level: Optional[str] = None
    rise_rate_ft_hr: Optional[float] = None
    hours_to_next: Optional[float] = None
    next_level: Optional[str] = None
    peak_cfs: Optional[float] = None
    warn_probability: Optional[float] = None
    discharge_cfs: Optional[float] = None


def _age_min(t: datetime) -> float:
    return (datetime.now(timezone.utc) - t).total_seconds() / 60.0


def _condition(stage: Optional[float], age_min: float) -> tuple[int, str]:
    """FIMAN-compatible CONDITION integer + text.

    Absence of data is never reported as Normal. This is the one place the
    state feed's behaviour is deliberately not copied: FIMAN currently
    reports CONDITION_TXT='Normal' for Jackson County gages whose last
    reading is months old.
    """
    if age_min > DEAD_MIN or stage is None:
        return 0, "No Data"
    if stage >= STATE_LEVELS_STAGE["MAJOR"]:
        return 4, "Major"
    if stage >= STATE_LEVELS_STAGE["MODERATE"]:
        return 3, "Moderate"
    if stage >= STATE_LEVELS_STAGE["MINOR"]:
        return 2, "Minor"
    if age_min > STALE_MIN:
        return 0, "No Data"
    return 1, "Normal"


def _local_level(stage: Optional[float]) -> str:
    if stage is None:
        return "UNKNOWN"
    if stage >= LOCAL_LEVELS["EMERGENCY"]:
        return "EMERGENCY"
    if stage >= LOCAL_LEVELS["WARNING"]:
        return "WARNING"
    if stage >= LOCAL_LEVELS["WATCH"]:
        return "WATCH"
    return "NORMAL"


def build_feature(key: str, r: Optional[Reading]) -> dict:
    s = SITES[key]
    # the surveyed number when it exists; the contested nominal only for
    # display in GAGE_DATUM, never for arithmetic
    datum = SURVEYED_DATUM if SURVEYED_DATUM is not None else s["GAGE_DATUM"]
    age = _age_min(r.observed_utc) if r else 1e9
    stage = r.stage_ft if (r and age <= DEAD_MIN) else None
    cond, cond_txt = _condition(stage, age)

    def msl(stage_ft: Optional[float]) -> Optional[float]:
        if SURVEYED_DATUM is None or stage_ft is None:
            return None
        return round(SURVEYED_DATUM + stage_ft, 2)

    props = {
        # ---- FIMAN-mirrored observation fields ----------------------------
        "SITE_ID": s["SITE_ID"],
        "NAME": s["NAME"],
        "COUNTY": s["COUNTY"],
        "OWNER": s["OWNER"],
        "LATITUDE": s["LATITUDE"],
        "LONGITUDE": s["LONGITUDE"],
        "GAGE_DATUM": datum,
        "River_Basin": s["River_Basin"],
        "WFO": s["WFO"],
        "SRV_INT": s["SRV_INT"],
        "IN_SERVICE": 1 if stage is not None else 0,
        "HYDRO_ALL_STAGE": None if stage is None else round(stage, 2),
        "CURRENT_ELEVATION_MSL": msl(stage),
        "BANK_FULL": msl(STATE_LEVELS_STAGE["BANK_FULL"]),
        "MINOR": msl(STATE_LEVELS_STAGE["MINOR"]),
        "MODERATE": msl(STATE_LEVELS_STAGE["MODERATE"]),
        "MAJOR": msl(STATE_LEVELS_STAGE["MAJOR"]),
        "CONDITION": cond,
        "CONDITION_TXT": cond_txt,
        "TREND": (r.trend if r else "Unknown"),
        "LAST_UPDATED": (r.observed_utc.isoformat() if r else None),
        "FORECASTPT": "False",

        # ---- explicit provenance / freshness ------------------------------
        "AGE_MINUTES": None if not r else round(age, 1),
        "IS_STALE": bool(age > STALE_MIN),
        "DATA_SOURCE": (r.source if r else "NONE"),
        "DATUM_VERIFIED": DATUM_VERIFIED,
        "DATUM_SOURCE": s["DATUM_SOURCE"],
        "ELEVATION_NOTE": (None if DATUM_VERIFIED else
                           "MSL fields suppressed: " + DATUM_NOTE
                           + ". Use HYDRO_ALL_STAGE and the FX_*_STAGE thresholds."),
        "REFERENCE_GAGE": s["REFERENCE_GAGE"],
        "AHPS_URL": f"https://water.noaa.gov/gauges/{s['AHPS_ID']}",

        # ---- FX_: modeled / forecast. NOT measurements. -------------------
        "FX_SHADOW_MODE": SHADOW_MODE,
        "FX_LEVEL": (r.level if (r and r.level) else _local_level(stage)),
        "FX_LEVEL_HYSTERETIC": bool(r and r.level),
        "FX_LEVEL_BASIS": (
            "Local basin model (TR-55 / Manning). NOT an NWS flood category. "
            "Local EMERGENCY (stage 11.0 ft) fires below the state MAJOR "
            "category (stage 15.0 ft) for the co-located NCEM gage 25380."
        ),
        "FX_WATCH_STAGE": LOCAL_LEVELS["WATCH"],
        "FX_WARNING_STAGE": LOCAL_LEVELS["WARNING"],
        "FX_EMERGENCY_STAGE": LOCAL_LEVELS["EMERGENCY"],
        "FX_RISE_RATE_FT_HR": None if not r else r.rise_rate_ft_hr,
        "FX_HOURS_TO_NEXT": None if not r else r.hours_to_next,
        "FX_NEXT_LEVEL": None if not r else r.next_level,
        "FX_PEAK_CFS": None if not r else r.peak_cfs,
        "FX_DISCHARGE_CFS": None if not r else r.discharge_cfs,
        "FX_WARN_PROBABILITY": None if not r else r.warn_probability,
    }

    return {
        "type": "Feature",
        "geometry": {"type": "Point",
                     "coordinates": [s["LONGITUDE"], s["LATITUDE"]]},
        "properties": props,
    }


def publish(readings: dict[str, Optional[Reading]], outdir: str = "docs") -> None:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    features = [build_feature(k, readings.get(k)) for k in SITES]
    fc = {
        "type": "FeatureCollection",
        "name": "Cullowhee-Weather modeled gages",
        "crs": {"type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }

    now = datetime.now(timezone.utc)
    meta = {
        "generated_utc": now.isoformat(),
        "shadow_mode": SHADOW_MODE,
        "authority_note": (
            "Not an official warning product. NWS (WFO GSP) is the warning "
            "authority; NCEM FIMAN is the authoritative gage record. This "
            "feed is a Jackson County forecast overlay intended to sit "
            "alongside FIMAN, not replace it."
        ),
        "publish_interval_hours": 0.5,
        "expected_next_utc": None,
        "site_count": len(features),
        "stale_sites": [f["properties"]["SITE_ID"] for f in features
                        if f["properties"]["IS_STALE"]],
        "schema": {
            "observation_fields": "mirror NCEM FIMAN GAGES_ALL naming",
            "forecast_fields": "prefixed FX_, modeled, not measured",
            "units": {"stage": "ft above GAGE_DATUM",
                      "elevation": "ft NAVD88",
                      "discharge": "cfs"},
        },
    }

    (out / "gages.geojson").write_text(json.dumps(fc, indent=2))
    (out / "feed_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"wrote {out/'gages.geojson'} ({len(features)} site(s)), "
          f"shadow_mode={SHADOW_MODE}")


if __name__ == "__main__":
    # Replace with a real call into flood_engine.assess(...) / sources.resolve(...)
    demo = Reading(
        stage_ft=2.4,
        observed_utc=datetime.now(timezone.utc),
        trend="Constant",
        source="GOV",
        rise_rate_ft_hr=0.0,
        hours_to_next=None,
        next_level="WATCH",
        peak_cfs=180.0,
        warn_probability=0.03,
        discharge_cfs=42.0,
    )
    publish({"CULLOWHEE_CK": demo})
