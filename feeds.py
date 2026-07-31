"""
feeds.py — NOAH · Cullowhee Creek — external live-data connectors
=================================================================
Wires every source from the 2026-07-30 data-source survey into the engine.
One module, stdlib + `requests` only for the live tier; heavier deps
(xarray/cfgrib/fsspec) are OPTIONAL and only needed for the gridded /
retrospective loaders, which degrade gracefully when absent.

PROVENANCE CONTRACT (matches basins.py):
  MEASURED      USGS IV, NWPS observed stage, ECONet, Synoptic/RAWS,
                MRMS gauge-corrected QPE, CoCoRaHS
  GOV_ESTIMATE  NWM (NWPS reach forecasts), gridded FFG, NWS alerts
  MODELED       everything NOAH derives internally (not produced here)

INTEGRATION POINTS
  live_inputs()        -> inputs_by_site dict shaped for
                          flood_network.routed_assessment()
  upwind_outlook()     -> the exact dict flood_network.tiered_posture()
                          expects as `upwind` (drop-in for gov_gauges)
  tuckasegee_context() -> downstream validation reference (NOT an input
                          to posture — display + validation only, per the
                          two-tier design note in flood_network.py)
  ffg_check()          -> independent posture cross-check (QPE/QPF vs FFG)
  nwm_forecast()       -> forecast-spine hydrograph per reach
  active_alerts()      -> official NWS FFA/FFW overlay

SELF-TEST:  python feeds.py         (prints per-connector status table)
NOTE: run the self-test from a normal network (campus/home). Cloud
sandboxes with allowlisted egress will show UNREACHABLE for everything.
"""

from __future__ import annotations

import gzip
import io
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

import requests

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
UA = "(WCU-NOAH/1.0 mickey.b.henson@gmail.com)"     # NWS requires a UA
LAT, LON = 35.307205, -83.182899                     # campus warning point
TIMEOUT = 20
CACHE_TTL_S = 240                                    # in-process cache

# --- API keys (env vars; leave unset until obtained) -----------------
SCO_KEY      = os.environ.get("NOAH_SCO_KEY", "")        # NC SCO Cardinal/CLOUDS (free academic)
SYNOPTIC_KEY = os.environ.get("NOAH_SYNOPTIC_KEY", "")   # synopticdata.com (free open-access tier)

# --- USGS / NWS identifiers (verified 2026-07-30) --------------------
USGS_UP   = "03508050"   # Tuckasegee R at SR 1172 nr Cullowhee (upstream of CC mouth)
USGS_DOWN = "03510577"   # Tuckasegee R at Barker's Creek (downstream)
NWS_LID_UP  = "TKRN7"    # = 03508050; action 13 / minor 16 / mod 19 / major 22 ft
NWS_LID_DAM = "TUKN7"    # Tuckasegee Dam stage (Duke release proxy)
NWM_REACH_AT_GAUGE = 19730552   # Tuckasegee at TKRN7
NWM_REACH_AT_DAM   = 19730568
# Cullowhee Creek's own NWM reach IDs: resolve once with resolve_reach(),
# then pin here. [SET after first successful resolve_reach() run]
NWM_REACH_CULLOWHEE = None

# --- Upwind approach arc (bearings FROM which storms arrive) ---------
# WNC flash-flood makers track in from SW-S-W (Gulf/orographic) — same
# convention as gov_gauges. Bearing is FROM the watershed TO the station.
UPWIND_SECTOR = (180.0, 292.5)          # S ... WNW, inclusive arc
UPWIND_STATIONS = {
    # id            name                        lat        lon       source   dist_mi
    "WINE": dict(name="Wayah Bald (ECONet)",    lat=35.1806, lon=-83.5622, src="econet", dist_mi=23.0),
    "WAYN": dict(name="Mtn Research Stn (ECONet)", lat=35.4875, lon=-82.9677, src="econet", dist_mi=17.0),
    # Synoptic sweep adds RAWS/HADS/PWS stations dynamically (see synoptic_sweep)
}
STORM_SPEED_MPH_DEFAULT = 25.0          # translate distance -> lead_min when no motion vector

# --- Basin centroids for grid sampling (MRMS/FFG) --------------------
# belk/double_springs/aahp match streamlit_app.COORDS. CC-* centroids are
# computed from the StreamStats watershed GeoJSONs when present locally.
BASIN_CENTROIDS = {
    "belk":           (35.3075, -83.1830),
    "double_springs": (35.2120, -83.1835),
    "aahp":           (35.2530, -83.2340),
}

def load_centroids_from_geojson(directory="."):
    """Average-of-vertices centroid for each CC-*.geojson StreamStats watershed.
    Cheap approximation (adequate for 1-km grid sampling)."""
    import glob
    for path in glob.glob(os.path.join(directory, "CC-*.geojson")):
        bid = os.path.basename(path).replace(".geojson", "")
        try:
            with open(path) as f:
                gj = json.load(f)
            xs, ys, n = 0.0, 0.0, 0
            def _walk(c):
                nonlocal xs, ys, n
                if isinstance(c[0], (int, float)):
                    xs += c[0]; ys += c[1]; n += 1
                else:
                    for cc in c:
                        _walk(cc)
            for feat in gj.get("features", []):
                _walk(feat["geometry"]["coordinates"])
            if n:
                BASIN_CENTROIDS[bid] = (round(ys / n, 5), round(xs / n, 5))
        except Exception:
            pass
    return BASIN_CENTROIDS


# ---------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept": "application/json"})
_cache: dict = {}

def _get(url, params=None, ttl=CACHE_TTL_S, as_json=True, headers=None):
    key = (url, tuple(sorted((params or {}).items())))
    hit = _cache.get(key)
    now = time.time()
    if hit and now - hit[0] < ttl:
        return hit[1]
    r = _session.get(url, params=params, timeout=TIMEOUT, headers=headers)
    r.raise_for_status()
    out = r.json() if as_json else r.content
    _cache[key] = (now, out)
    return out


def _bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2, degrees clockwise from N."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _in_upwind_sector(bearing):
    lo, hi = UPWIND_SECTOR
    return lo <= bearing <= hi if lo <= hi else (bearing >= lo or bearing <= hi)


# =====================================================================
# 1. USGS INSTANTANEOUS VALUES  [MEASURED]
# =====================================================================
def usgs_iv(sites=(USGS_UP, USGS_DOWN), period="PT6H",
            params="00060,00065"):
    """15-min stage/discharge. Returns {site_no: {name, series:{code:[(epoch,val)]},
    latest:{code:(epoch,val)}}}."""
    j = _get("https://waterservices.usgs.gov/nwis/iv/",
             {"format": "json", "sites": ",".join(sites),
              "parameterCd": params, "period": period, "siteStatus": "all"})
    out = {}
    for ts in j["value"]["timeSeries"]:
        sno = ts["sourceInfo"]["siteCode"][0]["value"]
        code = ts["variable"]["variableCode"][0]["value"]
        pts = [(int(datetime.fromisoformat(v["dateTime"]).timestamp()),
                float(v["value"]))
               for v in ts["values"][0]["value"] if v["value"] not in ("", None)]
        d = out.setdefault(sno, {"name": ts["sourceInfo"]["siteName"],
                                 "series": {}, "latest": {}})
        d["series"][code] = pts
        if pts:
            d["latest"][code] = pts[-1]
    return out


def tuckasegee_context():
    """Downstream validation reference for the dashboard. NOT a posture input
    (two-tier design: the gauged mainstem validates, it does not drive)."""
    g = usgs_iv()
    up, down = g.get(USGS_UP, {}), g.get(USGS_DOWN, {})
    q_up = (up.get("latest", {}).get("00060") or (None, None))[1]
    q_dn = (down.get("latest", {}).get("00060") or (None, None))[1]
    local_inflow = round(q_dn - q_up, 1) if (q_up is not None and q_dn is not None) else None
    return {
        "upstream":   {"site": USGS_UP, "lid": NWS_LID_UP, **up.get("latest", {}),
                       "name": up.get("name"),
                       "flood_categories_ft": {"action": 13, "minor": 16,
                                               "moderate": 19, "major": 22}},
        "downstream": {"site": USGS_DOWN, "name": down.get("name"),
                       **down.get("latest", {})},
        # crude, unlagged; Cullowhee Cr + Scott Cr + Savannah Cr + reach gains
        "local_inflow_cfs": local_inflow,
        "stage_series_up":   up.get("series", {}).get("00065", []),
        "stage_series_down": down.get("series", {}).get("00065", []),
        "provenance": "MEASURED",
    }


# =====================================================================
# 2. NWPS — GAUGES + NWM REACH FORECASTS  [MEASURED / GOV_ESTIMATE]
# =====================================================================
NWPS = "https://api.water.noaa.gov/nwps/v1"

def nwps_gauge(lid=NWS_LID_UP):
    """Observed stage + flood categories + any published crests for an NWS LID."""
    return _get(f"{NWPS}/gauges/{lid}")


def nwps_stageflow(lid=NWS_LID_UP):
    """Observed (and forecast, when NWS issues one) stage/flow series."""
    return _get(f"{NWPS}/gauges/{lid}/stageflow")


def nwm_forecast(reach_id=NWM_REACH_AT_GAUGE, series="short_range"):
    """NWM hydrograph for a reach via NWPS. series: short_range | medium_range |
    long_range | analysis_assimilation. Returns [(epoch, cfs)]."""
    j = _get(f"{NWPS}/reaches/{reach_id}/streamflow", {"series": series})
    out = []
    units = {"u": "cfs"}

    def _walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("units"), str):
                units["u"] = node["units"]
            data = node.get("data")
            if isinstance(data, list):
                for p in data:
                    try:
                        t = int(datetime.fromisoformat(
                            str(p["validTime"]).replace("Z", "+00:00")).timestamp())
                        out.append((t, float(p["flow"])))
                    except Exception:
                        continue
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, (dict, list)):
                    _walk(v)

    _walk(j or {})
    scale = 1000.0 if units["u"].lower().startswith("kcfs") else 1.0
    return sorted({(t, round(v * scale, 1)) for t, v in out})


def resolve_reach(lat=LAT, lon=LON):
    """USGS NLDI: point -> NHDPlus COMID (= NWM reach/feature id)."""
    j = _get("https://api.water.usgs.gov/nldi/linked-data/comid/position",
             {"coords": f"POINT({lon} {lat})"})
    feats = j.get("features", [])
    return int(feats[0]["properties"]["comid"]) if feats else None


# =====================================================================
# 3. NWS ALERTS  [GOV_ESTIMATE / official]
# =====================================================================
def active_alerts(lat=LAT, lon=LON):
    """Active NWS alerts at the warning point. FFW/FFA/FLW filtered first."""
    j = _get("https://api.weather.gov/alerts/active", {"point": f"{lat},{lon}"},
             headers={"Accept": "application/geo+json"})
    out = []
    for f in j.get("features", []):
        p = f["properties"]
        out.append({"event": p.get("event"), "severity": p.get("severity"),
                    "onset": p.get("onset"), "ends": p.get("ends"),
                    "headline": p.get("headline"), "id": p.get("id")})
    flood_first = sorted(out, key=lambda a: 0 if "Flood" in (a["event"] or "") else 1)
    return flood_first


# =====================================================================
# 4. GRIDDED FLASH FLOOD GUIDANCE  [GOV_ESTIMATE]
# =====================================================================
FFG_MS = ("https://mapservices.weather.noaa.gov/raster/rest/services/"
          "precip/rfc_gridded_ffg/MapServer")

def ffg_at(lat=LAT, lon=LON):
    """SERFC gridded FFG sampled at a point -> {'ffg1h':in,'ffg3h':in,'ffg6h':in}.

    The service nests each duration as a GROUP layer ("Flash Flood Guidance
    01 Hour") whose queryable raster is its "Image" CHILD layer — identify on
    the group returns nothing (the all-None bug seen 2026-07-30). So: find the
    groups, then resolve each to its Image child via parentLayerId, and parse
    any attribute that looks like a pixel value."""
    info = _get(f"{FFG_MS}", {"f": "json"})
    layers = info.get("layers", [])
    groups = {}                                   # group layer id -> our key
    for lyr in layers:
        nm = str(lyr.get("name", "")).lower()
        for tag, key in (("01", "ffg1h"), ("03", "ffg3h"), ("06", "ffg6h")):
            if f"{tag} hour" in nm:
                groups[lyr["id"]] = key
    target = {}                                   # our key -> layer id to query
    for lyr in layers:
        if (str(lyr.get("name", "")).strip().lower() == "image"
                and lyr.get("parentLayerId") in groups):
            target[groups[lyr["parentLayerId"]]] = lyr["id"]
    for gid, key in groups.items():               # fallback: query the group
        target.setdefault(key, gid)

    out = {}
    for key, lid in target.items():
        j = _get(f"{FFG_MS}/identify",
                 {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint",
                  "sr": 4326, "layers": f"all:{lid}", "tolerance": 1,
                  "mapExtent": f"{lon-.1},{lat-.1},{lon+.1},{lat+.1}",
                  "imageDisplay": "400,400,96", "returnGeometry": "false",
                  "f": "json"})
        val = None
        for res in j.get("results", []):
            att = res.get("attributes") or {}
            # exact data-value key first; NEVER the renderer's 0-255 stretch
            # ("Stretched.Pixel Value" published 53/69/90-inch nonsense 2026-07-31)
            candidates = [v for k, v in att.items()
                          if k.strip().lower() == "pixel value"]
            candidates += [v for k, v in att.items()
                           if "pixel" in k.lower()
                           and "stretch" not in k.lower()
                           and k.strip().lower() != "pixel value"]
            for v in candidates:
                if v in (None, "", "NoData", "NaN"):
                    continue
                try:
                    val = float(v)
                    break
                except (TypeError, ValueError):
                    continue
            if val is not None:
                break
        # plausibility gate: FFG is inches of rain-to-flood; anything outside
        # (0, 15] is a units/renderer artifact — publish nothing rather than
        # a wrong number (the panel hides the row on None).
        out[key] = val if (val is not None and 0.0 < val <= 15.0) else None
    return out


def ffg_check(storm_rain_in_by_window, lat=LAT, lon=LON):
    """Compare observed/forecast rain vs FFG. storm_rain_in_by_window:
    {'1h':x,'3h':y,'6h':z}. Returns exceedance ratios (>=1.0 -> flash-flood
    capable rain per the RFC's own guidance) — an independent cross-check on
    NOAH's posture, never a substitute for it."""
    ffg = ffg_at(lat, lon)
    out = {}
    for w, key in (("1h", "ffg1h"), ("3h", "ffg3h"), ("6h", "ffg6h")):
        rain, g = storm_rain_in_by_window.get(w), ffg.get(key)
        out[w] = round(rain / g, 2) if (rain is not None and g) else None
    out["ffg_in"] = ffg
    out["provenance"] = "GOV_ESTIMATE"
    return out


# =====================================================================
# 5. MRMS GAUGE-CORRECTED QPE  [MEASURED]
# =====================================================================
MRMS_BUCKET = "https://noaa-mrms-pds.s3.amazonaws.com"
MRMS_PRODUCT = "CONUS/MultiSensor_QPE_01H_Pass2_00.00"   # gauge-corrected hourly

def mrms_latest_key():
    """Newest object key for the gauge-corrected 1-h QPE via S3 list API."""
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    xml = _get(MRMS_BUCKET, {"list-type": "2",
                             "prefix": f"{MRMS_PRODUCT}/{day}/"},
               as_json=False).decode()
    keys = [s.split("</Key>")[0] for s in xml.split("<Key>")[1:]]
    return sorted(keys)[-1] if keys else None


def mrms_qpe_at_basins(centroids=None):
    """Sample latest gauge-corrected 1-h QPE (mm) at each basin centroid.
    Requires xarray+cfgrib (optional). Returns {basin_id: qpe_in} or
    {'error': ...} when the grib stack is absent.
    WNC caveat: KGSP beam blockage — treat radar-only values with suspicion;
    this product is the gauge-CORRECTED pass for that reason, and local bias
    correction against ECONet/CoCoRaHS/NOAH gauges is still recommended."""
    centroids = centroids or BASIN_CENTROIDS
    key = mrms_latest_key()
    if not key:
        return {"error": "no MRMS object listed for today"}
    raw = _get(f"{MRMS_BUCKET}/{key}", as_json=False, ttl=600)
    try:
        import tempfile
        import xarray as xr
        buf = gzip.decompress(raw) if key.endswith(".gz") else raw
        with tempfile.NamedTemporaryFile(suffix=".grib2") as tf:
            tf.write(buf); tf.flush()
            ds = xr.open_dataset(tf.name, engine="cfgrib",
                                 backend_kwargs={"indexpath": ""})
            var = list(ds.data_vars)[0]
            out = {}
            for bid, (la, lo) in centroids.items():
                v = float(ds[var].sel(latitude=la, longitude=lo % 360,
                                      method="nearest").values)
                out[bid] = round(max(v, 0.0) / 25.4, 3)     # mm -> in
            out["_key"] = key
            out["provenance"] = "MEASURED (gauge-corrected radar)"
            return out
    except ImportError:
        return {"error": "install xarray+cfgrib for MRMS sampling",
                "latest_key": key}
    except Exception as e:
        return {"error": f"MRMS decode failed: {e}", "latest_key": key}


# =====================================================================
# 6. NC ECONet (SCO CLOUDS API)  [MEASURED — needs free academic key]
# =====================================================================
SCO_API = "https://api.climate.ncsu.edu"

def econet_obs(loc="WAYN", hours=3, key=SCO_KEY):
    """Recent precip + soil moisture from an ECONet station. Requires an SCO
    API hash (free for university use — request via climate.ncsu.edu).
    Returns {'rain_in': total, 'rate_in_hr': latest, 'soil_pct': latest} or
    {'error': 'needs_key'}."""
    if not key:
        return {"error": "needs_key",
                "how": "request free academic hash at climate.ncsu.edu/cronos → set NOAH_SCO_KEY"}
    end = datetime.now()
    start = end - timedelta(hours=hours)
    j = _get(f"{SCO_API}/data",
             {"loc": f"location={loc}", "var": "precip1m,vwc10cm",
              "start": start.strftime("%Y-%m-%d %H:%M"),
              "end": end.strftime("%Y-%m-%d %H:%M"),
              "obtype": "M", "output": "json", "hash": key})
    try:
        rows = j["data"]
        rain = sum(float(r.get("precip1m") or 0) for r in rows)
        soil_vals = [float(r["vwc10cm"]) for r in rows if r.get("vwc10cm")]
        last_hr = [float(r.get("precip1m") or 0) for r in rows[-60:]]
        return {"rain_in": round(rain, 3),
                "rate_in_hr": round(sum(last_hr), 3),
                "soil_pct": round(soil_vals[-1] * 100.0, 1) if soil_vals else None,
                "provenance": "MEASURED"}
    except Exception as e:
        return {"error": f"econet parse: {e}"}


# =====================================================================
# 7. SYNOPTIC DATA SWEEP — RAWS/HADS/PWS in one call  [MEASURED — needs free token]
# =====================================================================
def synoptic_sweep(radius_mi=30, token=SYNOPTIC_KEY):
    """Every public surface station within radius of campus: 1-h precip.
    Free open-access token at synopticdata.com. Returns list of
    {stid, name, lat, lon, dist_mi, bearing, upwind, precip_1h_in}."""
    if not token:
        return {"error": "needs_key",
                "how": "free open-access token at synopticdata.com → set NOAH_SYNOPTIC_KEY"}
    j = _get("https://api.synopticdata.com/v2/stations/timeseries",
             {"radius": f"{LAT},{LON},{radius_mi}", "recent": 90,
              "vars": "precip_accum_one_hour", "units": "english",
              "token": token})
    out = []
    for s in j.get("STATION", []):
        try:
            la, lo = float(s["LATITUDE"]), float(s["LONGITUDE"])
            vals = s["OBSERVATIONS"].get("precip_accum_one_hour_set_1", [])
            p1h = float(vals[-1]) if vals else None
            brg = _bearing_deg(LAT, LON, la, lo)
            out.append({"stid": s["STID"], "name": s["NAME"], "lat": la, "lon": lo,
                        "dist_mi": round(float(s.get("DISTANCE", 0)), 1),
                        "bearing": round(brg), "upwind": _in_upwind_sector(brg),
                        "precip_1h_in": p1h})
        except Exception:
            continue
    return out


# =====================================================================
# 8. UPWIND OUTLOOK  [MEASURED] — drop-in for gov_gauges.upwind_outlook()
# =====================================================================
RAIN_RATE_WATCH_IN_HR = 0.5     # tunable; calibrate vs Helene (per MODEL_PROVENANCE)
RAIN_RATE_MAX_IN_HR = 1.5

def upwind_outlook(storm_speed_mph=STORM_SPEED_MPH_DEFAULT):
    """Measured rain on the storm-approach arc -> the `upwind` dict that
    flood_network.tiered_posture() accepts. Sources: ECONet stations (keyed)
    + Synoptic sweep (keyed). Degrades to empty risk when nothing reports."""
    contributors = []

    for sid, meta in UPWIND_STATIONS.items():
        brg = _bearing_deg(LAT, LON, meta["lat"], meta["lon"])
        obs = econet_obs(sid) if meta["src"] == "econet" else {}
        rate = obs.get("rate_in_hr")
        if rate is None:
            continue
        score = max(0.0, min(1.0, (rate - 0.05) /
                             (RAIN_RATE_MAX_IN_HR - 0.05)))
        contributors.append({
            "area": meta["name"], "dir": _dir8(brg),
            "h1": rate, "h3": obs.get("rain_in"),
            "score": round(score, 2), "upwind": _in_upwind_sector(brg),
            "eta_min": int(meta["dist_mi"] / storm_speed_mph * 60.0)})

    sweep = synoptic_sweep()
    if isinstance(sweep, list):
        for s in sweep:
            if not s["upwind"] or not s["precip_1h_in"]:
                continue
            score = max(0.0, min(1.0, (s["precip_1h_in"] - 0.05) /
                                 (RAIN_RATE_MAX_IN_HR - 0.05)))
            if score <= 0:
                continue
            contributors.append({
                "area": s["name"], "dir": _dir8(s["bearing"]),
                "h1": s["precip_1h_in"], "h3": None, "score": round(score, 2),
                "upwind": True,
                "eta_min": int(s["dist_mi"] / storm_speed_mph * 60.0)})

    up = [c for c in contributors if c["upwind"] and c["score"] > 0]
    if not up:
        return {"risk": 0.0, "level": "NORMAL", "lead_min": None,
                "contributors": contributors, "note": "No measured upwind rain."}
    # noisy-OR of station scores, same combine rule the engine uses
    acc = 1.0
    for c in up:
        acc *= (1.0 - c["score"])
    risk = round(1.0 - acc, 3)
    lead = min(c["eta_min"] for c in up)
    best = max(up, key=lambda c: c["score"])
    return {"risk": risk, "level": "WATCH" if risk >= 0.45 else "NORMAL",
            "lead_min": lead, "contributors": contributors,
            "note": (f"MEASURED rain {best['h1']:.2f} in/hr at {best['area']} "
                     f"({best['dir']}), ~{lead} min out at {storm_speed_mph:.0f} mph.")}


def _dir8(bearing):
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((bearing + 22.5) // 45) % 8]


# =====================================================================
# 9. DUKE ENERGY LAKES  [context; best-effort — endpoint unofficial]
# =====================================================================
def duke_lakes():
    """Lake levels / flow releases for the Tuckasegee chain. Explains non-rain
    stage changes at the mainstem gauges. Endpoint is unofficial and may move;
    wrapped so failure is silent."""
    for url in ("https://lakes.hydro-derived.duke-energy.app/api/lakes",
                "https://lakes.duke-energy.com/api/lakes"):
        try:
            j = _get(url, ttl=900)
            keep = [x for x in (j if isinstance(j, list) else j.get("lakes", []))
                    if any(k in str(x.get("name", "")).lower()
                           for k in ("glenville", "tuckasegee", "bear creek",
                                     "cedar cliff", "wolf creek", "thorpe"))]
            if keep:
                return {"lakes": keep, "provenance": "MEASURED (operator)"}
        except Exception:
            continue
    return {"error": "duke endpoint not reachable/parsable — check Lake View app API",
            "fallback": "TUKN7 dam stage via nwps_gauge('TUKN7') is the reliable proxy"}


# =====================================================================
# 10. CoCoRaHS DAILY  [MEASURED, daily — QPE bias-check only]
# =====================================================================
def cocorahs_jackson(days=1):
    """Yesterday's daily reports for Jackson County NC (7am totals). Legacy CSV
    export; for QPE bias-checking and event reconstruction, not warning."""
    end = datetime.now()
    start = end - timedelta(days=days)
    try:
        raw = _get("https://data.cocorahs.org/export/exportreports.aspx",
                   {"ReportType": "Daily", "Format": "CSV", "State": "NC",
                    "County": "Jackson",
                    "ReportDateType": "reportdate",
                    "StartDate": start.strftime("%m/%d/%Y"),
                    "EndDate": end.strftime("%m/%d/%Y")},
                   as_json=False, ttl=3600).decode(errors="replace")
        lines = [l for l in raw.splitlines() if l.strip()]
        return {"csv_rows": len(lines) - 1, "raw": raw[:5000],
                "provenance": "MEASURED (daily)"}
    except Exception as e:
        return {"error": f"cocorahs export: {e}"}


# =====================================================================
# 11. OFFLINE / CALIBRATION LOADERS  (heavy deps optional)
# =====================================================================
def aorc_point_series(lat=LAT, lon=LON, year=2024):
    """AORC hourly precip (kg/m^2 ~ mm) at a point for one year, via the
    noaa-nws-aorc zarr on AWS. Needs xarray+fsspec+s3fs. ~seconds per year.
    Use: retrospective forcing (e.g. Helene reconstruction with the WCU
    5-min sensor records as truth)."""
    try:
        import xarray as xr
        ds = xr.open_zarr(f"s3://noaa-nws-aorc-v1-1-1km/{year}.zarr",
                          storage_options={"anon": True}, consolidated=True)
        pt = ds["APCP_surface"].sel(latitude=lat, longitude=lon, method="nearest")
        return pt.to_series()
    except ImportError:
        return {"error": "install xarray fsspec s3fs zarr for AORC access"}


def nwm_retrospective_flow(reach_id, start="1979-02-01", end="2023-01-31"):
    """NWM v3 CONUS retrospective (s3://noaa-nwm-retrospective-3-0) chrtout
    zarr — 40+ yr modeled flow for a reach. Benchmark distribution + an
    independent arbiter for the Long Branch TVA-vs-StreamStats disagreement."""
    try:
        import xarray as xr
        ds = xr.open_zarr(
            "s3://noaa-nwm-retrospective-3-0/CONUS/zarr/chrtout.zarr",
            storage_options={"anon": True}, consolidated=True)
        q = ds["streamflow"].sel(feature_id=reach_id).sel(time=slice(start, end))
        return (q * 35.3147).to_series()          # m3/s -> cfs
    except ImportError:
        return {"error": "install xarray fsspec s3fs zarr for NWM retrospective"}


# =====================================================================
# 12. ONE-CALL BUNDLE for streamlit_app
# =====================================================================
def live_inputs():
    """Assemble everything reachable right now into one bundle:
      inputs_by_site : rain (MRMS QPE per centroid) — stage/soil stay None
                       until NOAH sensors ship (never fabricated here)
      upwind         : for tiered_posture(..., upwind=...)
      context        : Tuckasegee gauges (validation display)
      alerts, ffg    : official overlays
      nwm            : forecast-spine hydrograph at the mainstem reach
      status         : per-connector OK/ERROR ledger for the provenance chips
    """
    bundle = {"inputs_by_site": {}, "status": {}}

    qpe = mrms_qpe_at_basins()
    bundle["status"]["mrms"] = "OK" if "_key" in qpe else qpe.get("error", "ERR")
    for sid in BASIN_CENTROIDS:
        rain = qpe.get(sid) if isinstance(qpe, dict) else None
        bundle["inputs_by_site"][sid] = {"storm_rain_in": rain,
                                         "soil_pct": None, "stage_series": None}

    soil = econet_obs("WAYN")
    bundle["status"]["econet"] = "OK" if "soil_pct" in soil else soil.get("error", "ERR")
    if soil.get("soil_pct") is not None:
        # regional antecedent proxy until in-basin soil sensors exist — tag it
        for sid in bundle["inputs_by_site"]:
            bundle["inputs_by_site"][sid]["soil_pct"] = soil["soil_pct"]
            bundle["inputs_by_site"][sid]["soil_src"] = "ECONet WAYN (regional proxy)"

    for name, fn in (("usgs_context", tuckasegee_context),
                     ("upwind", upwind_outlook),
                     ("alerts", active_alerts),
                     ("ffg", ffg_at),
                     ("duke", duke_lakes)):
        try:
            bundle[name] = fn()
            err = isinstance(bundle[name], dict) and bundle[name].get("error")
            bundle["status"][name] = err or "OK"
        except Exception as e:
            bundle[name] = None
            bundle["status"][name] = f"ERR {e}"

    try:
        bundle["nwm"] = nwm_forecast(NWM_REACH_CULLOWHEE or NWM_REACH_AT_GAUGE)
        bundle["status"]["nwm"] = "OK" if bundle["nwm"] else "EMPTY"
    except Exception as e:
        bundle["nwm"], bundle["status"]["nwm"] = None, f"ERR {e}"

    return bundle


# =====================================================================
# SELF-TEST
# =====================================================================
def _selftest():
    print("NOAH feeds self-test —", datetime.now().isoformat(timespec="seconds"))
    print("-" * 72)
    tests = [
        ("USGS IV (03508050+03510577)", lambda: usgs_iv()),
        ("Tuckasegee context",          tuckasegee_context),
        ("NWPS gauge TKRN7",            lambda: nwps_gauge("TKRN7")),
        ("NWPS gauge TUKN7 (dam)",      lambda: nwps_gauge("TUKN7")),
        ("NWM short-range @19730552",   lambda: nwm_forecast()),
        ("NLDI reach resolve (campus)", lambda: resolve_reach()),
        ("NWS active alerts",           active_alerts),
        ("Gridded FFG",                 ffg_at),
        ("MRMS latest key",             mrms_latest_key),
        ("MRMS QPE @ centroids",        mrms_qpe_at_basins),
        ("ECONet WAYN",                 lambda: econet_obs("WAYN")),
        ("Synoptic sweep 30 mi",        synoptic_sweep),
        ("Upwind outlook",              upwind_outlook),
        ("Duke lakes",                  duke_lakes),
        ("CoCoRaHS Jackson Co",         cocorahs_jackson),
    ]
    for name, fn in tests:
        t0 = time.time()
        try:
            r = fn()
            err = isinstance(r, dict) and r.get("error")
            status = f"NEEDS-KEY/ERR: {err}" if err else "OK"
            extra = ""
            if name.startswith("USGS IV") and isinstance(r, dict):
                for s, d in r.items():
                    g = d["latest"].get("00065", (None, None))[1]
                    q = d["latest"].get("00060", (None, None))[1]
                    extra += f"  [{s}: {g} ft, {q} cfs]"
            if name == "NWM short-range @19730552" and isinstance(r, list):
                extra = f"  [{len(r)} pts]"
        except Exception as e:
            status, extra = f"UNREACHABLE ({type(e).__name__})", ""
        print(f"{name:<30} {status:<40} {time.time()-t0:4.1f}s{extra}")
    print("-" * 72)
    print("Reminder: NOAH_SCO_KEY / NOAH_SYNOPTIC_KEY unlock ECONet + Synoptic.")


if __name__ == "__main__":
    _selftest()
