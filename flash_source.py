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
a ~214 MB GRIB2 bundle and decoding it, this asks the THREDDS NetCDF Subset
Service for a single grid cell as CSV — a few hundred bytes per basin.

STATUS: UNVERIFIED AGAINST THE LIVE SERVICE.
The variable names, grid geometry and CSV output option below were read off
the server's own NCSS form. The end-to-end HTTP handshake was NOT completed —
it was authored from a sandbox with no egress to that host. So this ships
BEHIND A FLAG and defaults to off:

    export NOAH_FLASH_ENABLED=1        # turn it on after the self-test passes

Run the self-test from campus (a normal network), same as feeds.py:

    python flash_source.py

It walks a small ladder of request shapes, prints the raw first line of each
response, and tells you which one the server accepts. Pin that shape in
_REQUEST_STYLES (put the winner first) and set the flag.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
UA = "(WCU-NOAH/1.0 mickey.b.henson@gmail.com)"
TIMEOUT = 25

# Off until the self-test passes on a real network. A connector that has
# never completed a single live request does not get to publish numbers to
# a public flood page by default.
ENABLED = os.environ.get("NOAH_FLASH_ENABLED", "").strip() not in ("", "0", "false", "False")

# UW-Madison AOS THREDDS — see DEPENDENCY RISK above.
TDS_BASE = os.environ.get(
    "NOAH_FLASH_TDS",
    "https://thredds.aos.wisc.edu/thredds/ncss/grid/grib/NCEP/MRMS/CONUS/FLASH",
)
FILE_FMT = "MRMS_CONUS_FLASH_%Y%m%d_%H00.grib2"   # hourly bundle, 10-min steps inside

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
_REQUEST_STYLES = [
    "time_last",     # explicit newest timestamp in the bundle
    "time_all",      # every step in the bundle, take the last row
    "bare",          # no time parameter at all
    "time_present",  # only correct while the current bundle is still filling
]
_LON_STYLES = ["signed", "east360"]

# Filled in by the first successful request so subsequent basins skip the ladder.
_learned: dict = {"style": None, "lon": None, "file": None}

# Circuit breaker. The ladder is 4 styles x 2 longitude conventions x 4 bundles
# = 32 attempts. Walking that for all 8 basins x 2 families on a day when the
# server is down would be 512 requests and could stall the publish Action for
# minutes. Once nothing has ever worked in this process and we have burned
# through one basin's worth of attempts, stop trying for the rest of the run.
_MAX_BLIND_ATTEMPTS = 40
_attempts_without_success = 0


def _bundle_names(now: Optional[datetime] = None, back_hours: int = 4):
    """Newest-first candidate bundle filenames."""
    now = now or datetime.now(timezone.utc)
    top = now.replace(minute=0, second=0, microsecond=0)
    return [(top - timedelta(hours=h)).strftime(FILE_FMT) for h in range(back_hours)]


def _params(varnames, lat, lon, style, lon_style, bundle):
    lon_out = lon if lon_style == "signed" else (lon % 360.0)
    p = {"latitude": f"{lat:.5f}", "longitude": f"{lon_out:.5f}", "accept": "csv",
         "var": ",".join(varnames)}
    if style == "time_all":
        p["time"] = "all"
    elif style == "time_present":
        p["time"] = "present"
    elif style == "time_last":
        # bundle name carries the hour; its last CREST step is :50, last QPE
        # step is :58. Ask for the hour's end and let NCSS snap to nearest.
        stamp = datetime.strptime(bundle, FILE_FMT).replace(tzinfo=timezone.utc)
        p["time"] = (stamp + timedelta(minutes=58)).strftime("%Y-%m-%dT%H:%M:%SZ")
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


def _fetch_family(varnames, lat, lon):
    """Try request shapes until one works. Returns (row, units, time_str, meta)."""
    global _attempts_without_success
    if _learned["style"] is None and _attempts_without_success >= _MAX_BLIND_ATTEMPTS:
        raise RuntimeError(
            f"FLASH endpoint unreachable — gave up after {_attempts_without_success} "
            "attempts with no successful request shape")

    styles = ([_learned["style"]] if _learned["style"] else []) + \
             [s for s in _REQUEST_STYLES if s != _learned["style"]]
    lons = ([_learned["lon"]] if _learned["lon"] else []) + \
           [l for l in _LON_STYLES if l != _learned["lon"]]
    bundles = ([_learned["file"]] if _learned["file"] else []) + \
              [b for b in _bundle_names() if b != _learned["file"]]

    last_err = None
    for bundle in bundles:
        url = f"{TDS_BASE}/{bundle}"
        for style in styles:
            for lon_style in lons:
                _attempts_without_success += 1
                try:
                    r = _session.get(url,
                                     params=_params(varnames, lat, lon,
                                                    style, lon_style, bundle),
                                     timeout=TIMEOUT)
                    if r.status_code != 200:
                        last_err = f"HTTP {r.status_code} [{style}/{lon_style}/{bundle}]"
                        continue
                    row, units, tstr = _parse_csv(r.text)
                    _learned.update(style=style, lon=lon_style, file=bundle)
                    _attempts_without_success = 0
                    return row, units, tstr, {"style": style, "lon": lon_style,
                                              "bundle": bundle}
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e} [{style}/{lon_style}/{bundle}]"
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
        "enabled": ENABLED,
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
        out["note"] = ("disabled — set NOAH_FLASH_ENABLED=1 after "
                       "`python flash_source.py` passes on a real network")
        return out

    units_seen, valid_times = {}, []
    for bid, (la, lo) in points.items():
        rec = {"name": BASIN_NAMES.get(bid, bid), "lat": la, "lon": lo}
        for fam, varnames in VAR_FAMILIES:
            try:
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

    # freshness from the newest valid time we actually parsed
    if valid_times:
        try:
            newest = max(valid_times)
            vt = datetime.fromisoformat(newest.replace("Z", "+00:00"))
            age = (now - vt).total_seconds() / 60.0
            out["valid_utc"] = vt.isoformat()
            out["age_min"] = round(age, 1)
            out["fresh"] = age <= FRESH_MIN
            if not out["fresh"]:
                out["note"] = f"stale — newest FLASH step is {age:.0f} min old"
        except Exception:
            out["note"] = "could not parse FLASH valid time"
    else:
        out["note"] = out["note"] or "no FLASH values returned"

    # A stale bundle must not present as current data on a public page.
    if not out["fresh"]:
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
            for style in _REQUEST_STYLES:
                for lon_style in _LON_STYLES:
                    p = _params(varnames, lat, lon, style, lon_style, bundle)
                    try:
                        r = _session.get(f"{TDS_BASE}/{bundle}", params=p, timeout=TIMEOUT)
                    except Exception as e:
                        print(f"  {bundle} {fam:<6} {style:<12} {lon_style:<8} "
                              f"EXC {type(e).__name__}")
                        continue
                    head = (r.text or "").splitlines()[:2]
                    print(f"  {bundle} {fam:<6} {style:<12} {lon_style:<8} "
                          f"HTTP {r.status_code}")
                    for h in head:
                        print(f"      | {h[:150]}")
                    if r.status_code == 200 and len(head) >= 2:
                        ok_shape = ok_shape or (style, lon_style, bundle)
        if ok_shape:
            break

    if not ok_shape:
        print("\nNo request shape worked. Check the dataset list at")
        print("  https://thredds.aos.wisc.edu/thredds/catalog/grib/NCEP/MRMS/"
              "CONUS/FLASH/catalog.html")
        print("and confirm the newest bundle filename, then adjust FILE_FMT.")
        return 1

    style, lon_style, bundle = ok_shape
    print(f"\nWORKING SHAPE: style={style} lon={lon_style} bundle={bundle}")
    print(f"  -> put \"{style}\" first in _REQUEST_STYLES and "
          f"\"{lon_style}\" first in _LON_STYLES")
    print("  -> then: export NOAH_FLASH_ENABLED=1\n")

    os.environ["NOAH_FLASH_ENABLED"] = "1"
    globals()["ENABLED"] = True
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
