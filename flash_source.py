"""
flash_source.py — NSSL FLASH gridded flash-flood products for the roster.

FLASH (Flooded Locations And Simulated Hydrographs) is NSSL/OU's operational
flash-flood system: the CREST distributed hydrologic model inside the EF5
framework, forced by MRMS QPE, 1-km/10-min over CONUS. Transitioned to NCEP
operations in 2016. It is aimed squarely at small, ungauged headwater streams
— NOAH's problem class — which makes it the most relevant free cross-check
available for the Cullowhee roster.

WHAT THIS IS FOR
----------------
A THIRD INDEPENDENT OPINION, nothing more. NOAH's two-tier design is not
negotiable here:

    FLASH is GOV_ESTIMATE. It is modeled. It never confirms anything.
    It may inform the OUTLOOK tier. It must never raise WARNING or
    EMERGENCY on its own — those require a measured stream rise.

The same rule already applied to NWM and gridded FFG. FLASH joins that layer,
it does not sit above it.

WHY UNIT STREAMFLOW IS THE PRODUCT THAT MATTERS
-----------------------------------------------
CREST Maximum Unit Streamflow is discharge per unit drainage area
(m3 s-1 km-2), so it is SCALE-FREE: the 0.5 mi2 Cox Branch headwater and the
23 mi2 full watershed are directly comparable against the same number. NWS
publishes thresholds for it (FLASH product poster, WDTD warning training):

    >= 2.0 m3 s-1 km-2   flash flooding likely      (~180 cfs mi-2)
    >= 6.0 m3 s-1 km-2   significant flooding likely (~540 cfs mi-2)

That is a citable, externally-authored benchmark — useful precisely because
7 of NOAH's own thr_ft values are still placeholders. The QPE/FFG ratio has
a published hit rate too: >120% captured 75% of observed flash-flood reports.

DEPENDENCY RISK — READ THIS BEFORE RELYING ON IT
-------------------------------------------------
The operational MRMS FLASH GRIB2 bundles are served here from a UNIVERSITY
THREDDS server (UW-Madison AOS), not from NOAA directly. As of 2026-08-02:
  * mrms.ncep.noaa.gov/data/ exposes 2D/, 3DRefl/, 3DRhoHV/, 3DZdr/,
    ProbSevere/, RIDGEII/ — no FLASH directory.
  * the noaa-mrms-pds AWS bucket does not advertise FLASH products.
  * flash.ou.edu (the historical viewer) did not resolve to FLASH content
    when checked.
So this connector depends on a third party's goodwill and uptime. That is
acceptable for a cross-check tier and NOT acceptable for anything load-bearing.
If a NOAA-hosted FLASH endpoint turns up, move to it.

FORCING CAVEAT
--------------
FLASH eats MRMS. MRMS in WNC sits in the recognized KGSP beam-blockage gap
(see feeds.py MRMS notes and the data source survey §2.1). A FLASH miss in
this watershed may be a radar miss, not a model miss. Treat low values as
weak evidence of safety; treat high values as worth a look.

NETWORK
-------
requests-only, deliberately: the publish job runs from a GitHub Action with
requirements.txt only (no xarray, no cfgrib, no eccodes). Instead of pulling
a ~214 MB GRIB2 bundle and decoding it, this reads one grid cell at a time
over HTTP — a few hundred bytes per basin.

Two services, tried in that order:

  1. WMS GetFeatureInfo  (PRIMARY)  — proven reachable. GetCapabilities lists
     all 19 FLASH layers and GetMap renders, so this is the path that is known
     to work end to end. It reads from exactly the grid the map draws, which
     means the table and the raster can never disagree.
  2. NCSS grid-as-point CSV (FALLBACK) — kept because it returns units in the
     response header, but its parameter syntax was never confirmed live.

Worth understanding why this must live server-side: the THREDDS host sends no
CORS headers. A browser may draw the imagery (images are exempt) but cannot
read numbers out of it — the standalone viewer's connection check reports
"Failed to fetch" against GetFeatureInfo, confirmed 2026-08-02. Python has no
such restriction. That asymmetry is the whole reason the sub-basin table is
computed here and published, rather than queried from the page.

TIMING — READ THIS BEFORE TREATING IT AS EARLY WARNING
-------------------------------------------------------
FLASH steps every 10 minutes. This path does not deliver that:

    model step        10 min
    GRIB2 bundling    hourly, posted near the END of the hour it covers
    this feed         republishes every 30 min
    the page          polls every 5 min

so what a reader sees is typically 0-35 minutes old. Headwater lead times in
this watershed are of the same order (~33 min from Speedwell to campus on the
last measured event), which means FLASH on this path CANNOT corroborate a
signal in time to act on it. It is after-the-fact corroboration and a
calibration benchmark. It is not a lead-time source. The published payload
carries this in the "timing" block so the panel states it rather than
implying ten-minute currency it does not have.

Getting the real 10-minute cadence would need a source that serves the
individual grids rather than hourly bundles — worth asking NSSL about in the
same conversation as a NOAA-hosted endpoint.

STATUS
------
The WMS endpoint, its layer names, the grid geometry and the CRS list were all
read off the server's own GetCapabilities document and are reliable. The
end-to-end handshake still has not been run from the authoring environment,
which has no egress to that host — so if something is wrong it will be in the
GetFeatureInfo parameter details, not in the endpoint or the layer names.

Failures are never silent: a run that returns nothing publishes a "diagnostic"
block carrying the attempt count and the real HTTP errors, and flash.html
renders it in place of the table.

To confirm in one run, from campus (a normal network):

    python flash_source.py

    export NOAH_FLASH_ENABLED=0        # to turn the whole thing off
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
UA = "(WCU-NOAH/1.0 mickey.b.henson@gmail.com)"
TIMEOUT = 25

# ON by default. The request shape has never been confirmed against the live
# service from the machine this was written on, so instead of shipping dark
# this tries a ladder of plausible shapes and reports what it found. If none
# work, the panel says so with the actual HTTP errors attached — a visible
# "this connector cannot reach its source" is more useful than an empty page.
# Set NOAH_FLASH_ENABLED=0 to turn it off.
ENABLED = os.environ.get("NOAH_FLASH_ENABLED", "1").strip() not in ("0", "false", "False", "no")

# UW-Madison AOS THREDDS — see DEPENDENCY RISK above.
TDS_BASE = os.environ.get(
    "NOAH_FLASH_TDS",
    "https://thredds.aos.wisc.edu/thredds/ncss/grid/grib/NCEP/MRMS/CONUS/FLASH",
)
FILE_FMT = "MRMS_CONUS_FLASH_%Y%m%d_%H00.grib2"   # hourly bundle, 10-min steps inside

# Same collection, WMS instead of NCSS. This is now the PRIMARY path.
#
# Why: the NCSS parameter syntax was never confirmed against the live service,
# but WMS demonstrably works -- GetCapabilities returns all 19 FLASH layers and
# GetMap renders. GetFeatureInfo reads a single grid cell from exactly the same
# grid the map draws, so the table and the raster can no longer disagree.
#
# A browser CANNOT use this: the server sends no CORS headers, so a page may
# draw the imagery but not read values out of it (confirmed 2026-08-02, the
# viewer's own connection check reports "Failed to fetch"). Python has no such
# restriction, which is exactly why this belongs in the publish job.
WMS_BASE = os.environ.get("NOAH_FLASH_WMS", TDS_BASE.replace("/ncss/grid/", "/wms/"))

# WMS layer names carry a vertical-level suffix that the NCSS variable names do not.
WMS_SUFFIX = "_surface"

# Grid: 0.01 deg, latitude 54.995 -> 20.005 N, longitude 230.005 -> 299.995 E.
# NOTE the longitude convention is 0-360, not -180..180. NCSS usually converts
# a negative longitude itself; _LON_STYLES below tries both so we do not have
# to guess.
GRID_DEG = 0.01

# --- Variables, verified against the server's NCSS form 2026-08-02 ----
# CRITICAL: these sit on TWO DIFFERENT TIME COORDINATES in the same file
# (reftime/time for the QPE family, reftime1/time1 for the CREST family).
# Asking for both families in one NCSS request is a 400. Always request by
# family — that is why VAR_FAMILIES is a list of groups, not a flat list.
VAR_FAMILIES = [
    # (family key, [variable names])
    ("crest", ["FLASH_CREST_MAXUNITSTREAMFLOW",
               "FLASH_CREST_MAXSTREAMFLOW",
               "FLASH_CREST_MAXSOILSAT"]),
    ("qpe",   ["FLASH_QPE_FFG01H",
               "FLASH_QPE_FFG03H",
               "FLASH_QPE_ARIMAX"]),
]

# --- Published NWS thresholds (FLASH product poster; WDTD training) ---
# Units m3 s-1 km-2. The poster gives the onset band as 2.0-2.5; we take the
# conservative end (2.0) as the trip point and keep the band for display.
UNITQ_LIKELY_BAND = (2.0, 2.5)
UNITQ_LIKELY = 2.0
UNITQ_SIGNIFICANT = 6.0
# QPE/FFG: >100 increasingly likely; >120 captured 75% of flash-flood reports.
FFG_RATIO_LIKELY = 100.0
FFG_RATIO_STRONG = 120.0

# Freshness. Bundles land hourly and the publish job runs every 30 min, so in
# normal operation the newest sample is 0-60 min old. 90 min allows one late
# bundle before we call it stale; past that the panel shows stale, not a number.
FRESH_MIN = 90.0

# Plausibility gates. A connector that publishes a wrong number to a public
# flood page is worse than one that publishes nothing — the FFG stretch-value
# bug on 2026-07-31 is the precedent this project already learned from.
UNITQ_MAX = 500.0        # m3 s-1 km-2; real events are single digits
FFG_RATIO_MAX = 2000.0   # percent
ARI_MAX = 1000.0         # years
SOILSAT_MAX = 100.0      # percent

# Basin label points, from cullowhee_subbasins.geojson. These are display
# label positions, adequate for sampling a 1-km grid; if you later want true
# area-weighted means, replace with feeds.load_centroids_from_geojson().
BASIN_POINTS = {
    "CC-UP-503":     (35.22542, -83.208),
    "CC-MS-1100":    (35.25500, -83.183),
    "CC-TIL-705":    (35.26433, -83.224),
    "CC-SPD-1830":   (35.28500, -83.198),
    "CC-COX-097":    (35.29592, -83.210),
    "CC-LB-171":     (35.30011, -83.222),
    "CC-WCU-2260":   (35.29000, -83.185),
    "CC-MOUTH-2340": (35.31800, -83.196),
}
BASIN_NAMES = {
    "CC-UP-503": "Mountain",
    "CC-MS-1100": "Mtn. Lower",
    "CC-TIL-705": "Tilley Creek",
    "CC-SPD-1830": "Speedwell",
    "CC-COX-097": "Cox Branch",
    "CC-LB-171": "Long Branch",
    "CC-WCU-2260": "WCU Campus",
    "CC-MOUTH-2340": "Mouth",
}

_session = requests.Session()
_session.headers.update({"User-Agent": UA})


# ---------------------------------------------------------------------
# request shapes
# ---------------------------------------------------------------------
# The server's "Grids as Points" form advertises accept=csv, but the exact
# accepted combination of time parameters was not confirmed live. Rather than
# guess once and fail silently, try a short ladder and remember what worked.
# Put the winner first after the self-test tells you which it is.
#
# Known failure modes this ladder is designed around, learned the hard way:
#   * asking for CREST and QPE variables in one request is a 400 — they sit on
#     different time coordinates (time vs time1) in the same file;
#   * time=present is a 400 once the bundle's hour has passed, because the
#     requested instant is outside the file's range;
#   * the CREST family's last step is :50 while the QPE family's is :58, so a
#     timestamp valid for one family can be out of range for the other. Hence
#     "time_mid" asks for :45, which is inside both.
_TIME_STYLES = [
    "temporal_all",   # temporal=all — the documented NCSS "every step" form
    "bare",           # no time parameter at all; server picks its default
    "time_mid",       # hour+45, inside both families' ranges
    "time_range",     # explicit time_start/time_end spanning the hour
    "time_present",   # only valid while the current bundle is still filling
]
_VAR_STYLES = [
    "comma",    # var=a,b,c
    "repeat",   # var=a&var=b&var=c
    "single",   # one request per variable — slowest, most likely to be accepted
]
_LON_STYLES = ["signed", "east360"]

# Filled in by the first successful request so every later basin skips straight
# to the shape that worked. "mode" records which service answered.
_learned: dict = {"mode": None, "time": None, "var": None, "lon": None, "file": None}

# Circuit breaker. The full ladder is 5 time x 3 var x 2 lon x 4 bundles = 120
# attempts. Walking that for 8 basins x 2 families on a day when the server is
# down would be nearly two thousand requests and would stall the publish
# Action. Once nothing has worked and we have burned one basin's worth of
# attempts, stop trying for the rest of the run and report why.
_MAX_BLIND_ATTEMPTS = 40
_attempts_without_success = 0
# Kept for the panel: the last few distinct failures, so a reader can see
# whether this is "server down" or "we are asking wrong".
_errors: list = []


def _note_error(msg: str) -> None:
    # Truncated and capped: this lands in feed/external.json, which is
    # committed to the repo every 30 minutes. Full request URLs repeated
    # eight times would be pure churn in the commit log.
    msg = msg if len(msg) <= 240 else msg[:237] + "..."
    if msg not in _errors:
        _errors.append(msg)
    del _errors[4:]


def _bundle_names(now: Optional[datetime] = None, back_hours: int = 4):
    """Newest-first candidate bundle filenames."""
    now = now or datetime.now(timezone.utc)
    top = now.replace(minute=0, second=0, microsecond=0)
    return [(top - timedelta(hours=h)).strftime(FILE_FMT) for h in range(back_hours)]


def _params(varnames, lat, lon, time_style, var_style, lon_style, bundle):
    lon_out = lon if lon_style == "signed" else (lon % 360.0)
    p = {"latitude": f"{lat:.5f}", "longitude": f"{lon_out:.5f}", "accept": "csv"}

    if var_style == "comma":
        p["var"] = ",".join(varnames)
    else:                       # "repeat" and "single" both pass a list
        p["var"] = list(varnames)

    hour = datetime.strptime(bundle, FILE_FMT).replace(tzinfo=timezone.utc)
    if time_style == "temporal_all":
        p["temporal"] = "all"
    elif time_style == "time_present":
        p["time"] = "present"
    elif time_style == "time_mid":
        p["time"] = (hour + timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif time_style == "time_range":
        p["time_start"] = hour.strftime("%Y-%m-%dT%H:%M:%SZ")
        p["time_end"] = (hour + timedelta(minutes=58)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # "bare" adds nothing
    return p


def _parse_csv(text):
    """NCSS grid-as-point CSV -> (row_dict, units_dict, time_str).

    Header looks like:
      time,latitude[unit="degrees_north"],...,FLASH_CREST_MAXUNITSTREAMFLOW[unit="m3 s-1 km-2"]
    We keep the units because the QPE/FFG ratio could plausibly be published
    as a percentage or a fraction, and guessing which would be exactly the
    kind of unit error that produced the 53-inch FFG bug.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError("CSV had no data rows")
    raw_cols = [c.strip() for c in lines[0].split(",")]
    names, units = [], {}
    for c in raw_cols:
        if "[" in c:
            nm = c.split("[", 1)[0].strip()
            u = c.split('unit="', 1)[-1].split('"', 1)[0] if 'unit="' in c else ""
            units[nm] = u
        else:
            nm = c
        names.append(nm)
    row = [c.strip() for c in lines[-1].split(",")]     # newest step last
    out = {}
    for k, v in zip(names, row):
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            out[k] = v
    return out, units, str(out.get("time", ""))


def _gate(val, lo, hi):
    """Publish None rather than an implausible number."""
    if val is None or isinstance(val, str):
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if v != v:                      # NaN
        return None
    return v if lo <= v <= hi else None


def _first(learned_key, options):
    """Whatever worked last time, then everything else."""
    won = _learned.get(learned_key)
    return ([won] if won in options else []) + [o for o in options if o != won]


def _attempt(varnames, lat, lon, time_style, var_style, lon_style, bundle):
    """One request. Returns (row, units, time_str) or raises."""
    global _attempts_without_success
    _attempts_without_success += 1
    r = _session.get(f"{TDS_BASE}/{bundle}",
                     params=_params(varnames, lat, lon, time_style,
                                    var_style, lon_style, bundle),
                     timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return _parse_csv(r.text)


# ---------------------------------------------------------------------
# WMS GetFeatureInfo — primary path
# ---------------------------------------------------------------------
_INFO_VALUE = re.compile(r"<value[^>]*>\s*([^<\s]+)\s*</value>", re.I)
# ncWMS spells the timestamp <time> in some builds and <iso8601> in others,
# and a few omit it entirely. Try both tags, then fall back to finding any
# ISO-8601 instant anywhere in the document.
_INFO_TIME = re.compile(
    r"<(?:time|iso8601)[^>]*>\s*([^<\s]+)\s*</(?:time|iso8601)>", re.I)
_ANY_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")


def _wms_point(layer, lat, lon, bundle):
    """One cell from one layer. Returns (value_or_None, time_str).

    A 101x101 frame centred on the point, queried at its middle pixel: the
    cell that contains the point, at roughly 1 km across a 0.08 deg box.
    """
    d = 0.04
    params = {
        "service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo",
        "layers": layer, "query_layers": layer,
        "crs": "CRS:84",
        "bbox": f"{lon-d},{lat-d},{lon+d},{lat+d}",
        "width": "101", "height": "101", "i": "50", "j": "50",
        "info_format": "text/xml",
    }
    r = _session.get(f"{WMS_BASE}/{bundle}", params=params, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    text = r.text or ""
    times = _INFO_TIME.findall(text) or _ANY_ISO.findall(text)
    vals = _INFO_VALUE.findall(text)
    if not vals:
        raise RuntimeError("no <value> in GetFeatureInfo response")
    raw = vals[-1]                       # newest step last
    if raw.lower() in ("none", "nan", "null", ""):
        return None, (times[-1] if times else "")
    try:
        v = float(raw)
    except ValueError:
        raise RuntimeError(f"unparseable value {raw!r}")
    if v != v:
        v = None
    return v, (times[-1] if times else "")


def _fetch_family_wms(varnames, lat, lon):
    """Same contract as _fetch_family, over WMS. Returns (row, units, time, meta)."""
    global _attempts_without_success
    if _learned["mode"] is None and _attempts_without_success >= _MAX_BLIND_ATTEMPTS:
        raise RuntimeError(
            f"gave up after {_attempts_without_success} attempts — WMS unreachable")
    row, tstr, got = {}, "", 0
    last_err = None
    for bundle in _first("file", _bundle_names()):
        row, tstr, got = {}, "", 0
        for v in varnames:
            _attempts_without_success += 1
            try:
                val, t = _wms_point(v + WMS_SUFFIX, lat, lon, bundle)
                row[v] = val
                tstr = t or tstr
                got += 1
            except Exception as e:
                last_err = f"{type(e).__name__}: {e} [wms/{v}/{bundle}]"
                _note_error(last_err)
        if got:
            _learned.update(mode="wms", file=bundle)
            _attempts_without_success = 0
            return row, {}, tstr, {"mode": "wms", "bundle": bundle}
    raise RuntimeError(last_err or "no WMS GetFeatureInfo response")


def _fetch_family(varnames, lat, lon):
    """Try request shapes until one works. Returns (row, units, time_str, meta)."""
    if _learned["time"] is None and _attempts_without_success >= _MAX_BLIND_ATTEMPTS:
        raise RuntimeError(
            f"gave up after {_attempts_without_success} attempts — no request "
            "shape accepted (see diagnostic)")

    last_err = None
    for bundle in _first("file", _bundle_names()):
        # Re-check mid-ladder, not just on entry: a fully unreachable server
        # would otherwise cost a whole 120-attempt sweep before we notice.
        if _learned["time"] is None and _attempts_without_success >= _MAX_BLIND_ATTEMPTS:
            break
        for time_style in _first("time", _TIME_STYLES):
            for var_style in _first("var", _VAR_STYLES):
                for lon_style in _first("lon", _LON_STYLES):
                    shape = f"{time_style}/{var_style}/{lon_style}/{bundle}"
                    try:
                        if var_style == "single":
                            # Merge one-variable responses into a single row.
                            row, units, tstr = {}, {}, ""
                            for v in varnames:
                                r_, u_, t_ = _attempt([v], lat, lon, time_style,
                                                      var_style, lon_style, bundle)
                                row.update(r_); units.update(u_); tstr = t_ or tstr
                            if not any(v in row for v in varnames):
                                raise RuntimeError("no requested variable in response")
                        else:
                            row, units, tstr = _attempt(varnames, lat, lon, time_style,
                                                        var_style, lon_style, bundle)
                        _learned.update(time=time_style, var=var_style,
                                        lon=lon_style, file=bundle)
                        globals()["_attempts_without_success"] = 0
                        return row, units, tstr, {"time": time_style, "var": var_style,
                                                  "lon": lon_style, "bundle": bundle}
                    except Exception as e:
                        last_err = f"{type(e).__name__}: {e} [{shape}]"
                        _note_error(last_err)
    raise RuntimeError(last_err or "no FLASH request shape succeeded")


# ---------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------
def classify(unit_q: Optional[float], ffg_ratio: Optional[float]) -> str:
    """Map FLASH values onto a plain label.

    Deliberately NOT one of NOAH's posture levels. This returns FLASH's own
    reading so the panel can show agreement or disagreement, not a level that
    could be mistaken for the engine's output.
    """
    if unit_q is not None and unit_q >= UNITQ_SIGNIFICANT:
        return "SIGNIFICANT"
    if unit_q is not None and unit_q >= UNITQ_LIKELY:
        return "LIKELY"
    if ffg_ratio is not None and ffg_ratio >= FFG_RATIO_STRONG:
        return "LIKELY"
    if ffg_ratio is not None and ffg_ratio >= FFG_RATIO_LIKELY:
        return "APPROACHING"
    if unit_q is None and ffg_ratio is None:
        return "NO DATA"
    return "BELOW"


def latest(points=None, now=None) -> dict:
    """FLASH values per sub-basin. Never raises; returns a status dict."""
    now = now or datetime.now(timezone.utc)
    points = points or BASIN_POINTS

    out = {
        "source": "NSSL FLASH (CREST/EF5, MRMS-forced) via MRMS CONUS FLASH GRIB2",
        "endpoint": TDS_BASE,
        "tier": "GOV_ESTIMATE",
        "posture_role": ("cross-check only — informs OUTLOOK, never raises "
                         "WARNING or EMERGENCY (those need a measured stream rise)"),
        "thresholds": {
            "unit_streamflow_m3s_km2": {
                "likely": UNITQ_LIKELY,
                "likely_band": list(UNITQ_LIKELY_BAND),
                "significant": UNITQ_SIGNIFICANT,
                "cite": "NWS FLASH product poster / WDTD warning training",
            },
            "qpe_ffg_ratio_pct": {
                "likely": FFG_RATIO_LIKELY,
                "strong": FFG_RATIO_STRONG,
                "cite": ">120% captured 75% of flash-flood reports",
            },
        },
        "caveat": ("MRMS-forced — WNC sits in the KGSP radar beam-blockage gap, "
                   "so a FLASH miss here may be a radar miss, not a model miss."),
        # How the number is made, and how old it can be. Published with the
        # data so the panel never has to hard-code it and it cannot drift out
        # of sync with the connector.
        "method": {
            "model": "CREST distributed hydrologic model, run inside NSSL's EF5 framework",
            "forcing": "MRMS multi-radar/multi-sensor QPE (gauge-corrected passes)",
            "native_resolution": "1 km grid, 10-minute time step, CONUS-wide",
            "operational_since": "transitioned to NCEP operations 2016",
            "sampling": ("one grid cell nearest each sub-basin label point, pulled "
                         "via the THREDDS NetCDF Subset Service — not an "
                         "area-weighted basin mean"),
        },
        "timing": {
            "model_step_min": 10,
            "bundle_interval_min": 60,
            "publish_interval_min": 30,
            "typical_age_min": "0-35",
            "fresh_gate_min": FRESH_MIN,
            "explanation": (
                "FLASH itself steps every 10 minutes, but the GRIB2 files are "
                "packaged hourly and post near the end of the hour they cover, "
                "and this feed republishes every 30 minutes. So what you see is "
                "typically 0-35 minutes old. FLASH's 10-minute cadence does not "
                "survive this path. Treat it as after-the-fact corroboration and "
                "as a calibration benchmark — not as a lead-time source, since "
                "headwater lead times here are of the same order as the lag."),
        },
        "enabled": ENABLED,
        "diagnostic": None,
        # The bundle we actually read, published so flash.html's WMS map can
        # start from a known-good filename instead of probing the hourly
        # rollover blind. Null when no request succeeded; the page falls back
        # to probing in that case.
        "bundle": None,
        "wms": {
            "base": TDS_BASE.replace("/ncss/grid/", "/wms/"),
            "layers": {
                "unit_streamflow": "FLASH_CREST_MAXUNITSTREAMFLOW_surface",
                "ffg_ratio_1h": "FLASH_QPE_FFG01H_surface",
                "ffg_ratio_3h": "FLASH_QPE_FFG03H_surface",
                "ari_max": "FLASH_QPE_ARIMAX_surface",
                "soil_sat": "FLASH_CREST_MAXSOILSAT_surface",
            },
        },
        "basins": {},
        # Set up front so every exit path — including the disabled and error
        # returns below — yields the same payload shape. A consumer that has
        # to guess whether a key exists will eventually guess wrong.
        "headline": "NO DATA",
        "fresh": False,
        "age_min": None,
        "valid_utc": None,
        "note": "",
    }

    if not ENABLED:
        out["note"] = "disabled — NOAH_FLASH_ENABLED is set to 0"
        return out

    # Reset per-run learning so a shape that worked an hour ago is retried
    # first but a stale bundle name does not pin us to a missing file.
    _errors.clear()
    globals()["_attempts_without_success"] = 0
    _learned["file"] = None

    units_seen, valid_times = {}, []
    for bid, (la, lo) in points.items():
        rec = {"name": BASIN_NAMES.get(bid, bid), "lat": la, "lon": lo}
        for fam, varnames in VAR_FAMILIES:
            try:
                # WMS first (proven reachable); NCSS ladder only if it fails.
                try:
                    row, units, tstr, meta = _fetch_family_wms(varnames, la, lo)
                except Exception:
                    if _learned["mode"] == "wms":
                        raise
                    row, units, tstr, meta = _fetch_family(varnames, la, lo)
                units_seen.update(units)
                if tstr:
                    valid_times.append(tstr)
                rec.setdefault("_meta", meta)
                for v in varnames:
                    rec[v] = row.get(v)
            except Exception as e:
                rec[f"{fam}_error"] = f"{type(e).__name__}: {e}"

        uq = _gate(rec.get("FLASH_CREST_MAXUNITSTREAMFLOW"), 0.0, UNITQ_MAX)
        r1 = _gate(rec.get("FLASH_QPE_FFG01H"), 0.0, FFG_RATIO_MAX)
        r3 = _gate(rec.get("FLASH_QPE_FFG03H"), 0.0, FFG_RATIO_MAX)
        ratio = max([x for x in (r1, r3) if x is not None], default=None)

        out["basins"][bid] = {
            "name": rec["name"],
            "unit_streamflow": round(uq, 3) if uq is not None else None,
            "unit_streamflow_units": units_seen.get("FLASH_CREST_MAXUNITSTREAMFLOW", ""),
            "streamflow_m3s": _gate(rec.get("FLASH_CREST_MAXSTREAMFLOW"), 0.0, 1e6),
            "soil_sat_pct": _gate(rec.get("FLASH_CREST_MAXSOILSAT"), 0.0, SOILSAT_MAX),
            "ffg_ratio_1h": r1,
            "ffg_ratio_3h": r3,
            "ffg_ratio_units": units_seen.get("FLASH_QPE_FFG01H", ""),
            "ari_max_yr": _gate(rec.get("FLASH_QPE_ARIMAX"), 0.0, ARI_MAX),
            "reading": classify(uq, ratio),
            "error": rec.get("crest_error") or rec.get("qpe_error"),
        }

    # Did we actually get numbers? This is the question that matters, and it
    # is NOT the same as "did the server tell us the timestamp". The first
    # live run returned real values for every basin and no <time> element,
    # and keying off the timestamp marked all of it STALE and hid it.
    got_values = any(
        b.get(k) is not None
        for b in out["basins"].values()
        for k in ("unit_streamflow", "ffg_ratio_1h", "ffg_ratio_3h",
                  "ari_max_yr", "soil_sat_pct", "streamflow_m3s")
    )

    # Valid time: prefer what the service reported, else derive it from the
    # bundle we read. The bundle covers one hour and its newest step lands at
    # about :50, which is a sound floor on how current the data can be.
    vt = None
    if valid_times:
        try:
            vt = datetime.fromisoformat(max(valid_times).replace("Z", "+00:00"))
        except Exception:
            vt = None
    if vt is None and _learned.get("file"):
        try:
            hour = datetime.strptime(_learned["file"], FILE_FMT).replace(tzinfo=timezone.utc)
            vt = min(hour + timedelta(minutes=50), now)
            out["valid_source"] = "derived from bundle hour (service reported no time)"
        except Exception:
            vt = None

    if vt is not None:
        age = (now - vt).total_seconds() / 60.0
        out["valid_utc"] = vt.isoformat()
        out["age_min"] = round(age, 1)
        out["fresh"] = age <= FRESH_MIN
        if not out["fresh"]:
            out["note"] = f"stale — newest FLASH step is {age:.0f} min old"
    elif got_values:
        # Values but no way to date them: report them, say so, do not pretend.
        out["fresh"] = True
        out["note"] = "values returned, but the service reported no valid time"
    else:
        out["note"] = out["note"] or "no FLASH values returned"

    # Diagnostic only when we genuinely got nothing — a run that returned
    # numbers should not ship an error dump alongside them.
    if not got_values:
        out["diagnostic"] = {
            "attempts": _attempts_without_success,
            "shapes_tried": {"time": _TIME_STYLES, "var": _VAR_STYLES,
                             "lon": _LON_STYLES},
            "bundles_tried": _bundle_names(),
            "errors": list(_errors),
            "hint": ("Run `python flash_source.py` from a normal network — it "
                     "prints each request and the server's first response line, "
                     "which is what identifies the accepted shape."),
        }
    elif _learned["time"]:
        out["diagnostic"] = {"working_shape": dict(_learned)}
    out["bundle"] = _learned.get("file")

    # A stale bundle must not present as current data on a public page. But
    # "we could not date it" is not the same as "it is old" — only overwrite
    # readings when there is an actual age past the gate.
    if not out["fresh"] and out["age_min"] is not None:
        for b in out["basins"].values():
            b["reading"] = "STALE" if b.get("reading") != "NO DATA" else "NO DATA"

    # Roster-wide headline: the worst reading anywhere upstream.
    rank = {"NO DATA": 0, "STALE": 0, "BELOW": 1, "APPROACHING": 2,
            "LIKELY": 3, "SIGNIFICANT": 4}
    readings = [b.get("reading", "NO DATA") for b in out["basins"].values()]
    out["headline"] = max(readings, key=lambda r: rank.get(r, 0)) if readings else "NO DATA"
    return out


# ---------------------------------------------------------------------
# self-test — run this from campus, not from a sandbox
# ---------------------------------------------------------------------
def _selftest() -> int:
    lat, lon = BASIN_POINTS["CC-WCU-2260"]
    print(f"FLASH self-test — sampling CC-WCU-2260 at {lat}, {lon}")
    print(f"endpoint: {TDS_BASE}\n")

    ok_shape = None
    for bundle in _bundle_names():
        for fam, varnames in VAR_FAMILIES:
            for time_style in _TIME_STYLES:
                for var_style in _VAR_STYLES:
                    for lon_style in _LON_STYLES:
                        vs = [varnames[0]] if var_style == "single" else varnames
                        p = _params(vs, lat, lon, time_style, var_style,
                                    lon_style, bundle)
                        tag = (f"  {bundle} {fam:<6} {time_style:<13}"
                               f"{var_style:<8}{lon_style:<9}")
                        try:
                            r = _session.get(f"{TDS_BASE}/{bundle}", params=p,
                                             timeout=TIMEOUT)
                        except Exception as e:
                            print(f"{tag}EXC {type(e).__name__}")
                            continue
                        head = (r.text or "").splitlines()[:2]
                        print(f"{tag}HTTP {r.status_code}")
                        for h in head:
                            print(f"      | {h[:150]}")
                        if r.status_code == 200 and len(head) >= 2 and not ok_shape:
                            ok_shape = (time_style, var_style, lon_style, bundle)
        if ok_shape:
            break

    if not ok_shape:
        print("\nNo request shape worked. Next things to check, in order:")
        print("  1. Is the newest bundle name right? Open")
        print("     https://thredds.aos.wisc.edu/thredds/catalog/grib/NCEP/MRMS/"
              "CONUS/FLASH/catalog.html")
        print("     and compare against FILE_FMT.")
        print("  2. Open the NCSS form, fill it in by hand, and copy the URL it")
        print("     builds — that is authoritative:")
        print(f"     {TDS_BASE}/{_bundle_names()[1]}/pointDataset.html")
        print("  3. Paste that URL's query string here and adjust _params().")
        return 1

    time_style, var_style, lon_style, bundle = ok_shape
    print(f"\nWORKING SHAPE: time={time_style} var={var_style} lon={lon_style}")
    print(f"  -> put \"{time_style}\" first in _TIME_STYLES,")
    print(f"     \"{var_style}\" first in _VAR_STYLES,")
    print(f"     \"{lon_style}\" first in _LON_STYLES")
    print("  (optional — the ladder finds it anyway, this just saves requests)\n")

    snap = latest()
    print(json.dumps({k: v for k, v in snap.items() if k != "basins"}, indent=2))
    for bid, b in snap["basins"].items():
        print(f"  {bid:<15} {str(b['reading']):<12} "
              f"unitQ={b['unit_streamflow']} "
              f"({b['unit_streamflow_units']}) "
              f"ffg1h={b['ffg_ratio_1h']} ari={b['ari_max_yr']}")
    print("\nSanity check before you trust it: unit streamflow on a dry day "
          "should be ~0, and well under 2.0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
