"""
gov_gauges.py  —  MEASURED rainfall from PUBLIC GOVERNMENT gauges on the storm
approach arc W / SW / S of Cullowhee. The measured counterweight to the modeled
upwind rain in live_rainfall.py.

WHY THIS EXISTS
  live_rainfall.py is honest that it runs in SHADOW MODE: every forecast
  (Open-Meteo, GFS, even NWS QPF) under-calls orographic mountain rainfall, and
  the upwind corridor is fed by that same model. This module puts REAL, logged,
  quality-controlled precip into that corridor, from gauges that already sit on
  the exact ground where our storms are born — the Highlands escarpment (S), the
  Franklin / Coweeta approach (SW), and the Nantahala / Cherokee side (W).

  Two payoffs:
    1. The arrival-ETA logic (live_rainfall.arrival_eta) can run on MEASURED rain
       instead of modeled — "a real gauge 30 km SW just logged 1.1 in, wind is
       pushing it toward you" instead of "the model thinks it's raining SW."
    2. qpf_bias() compares each measured gauge against the model's own estimate
       for the same point — the live under-catch signal that tells us how much to
       distrust the forecast RIGHT NOW. This is the number that lets NOAH climb
       out of shadow mode.

WHY GOVERNMENT GAUGES (vs the Ambient map)
  These are professionally sited, calibrated, quality-controlled, and — crucially
  — reachable network-wide through ONE free API each, with no per-owner keys and
  none of the sun-baked-siting noise a private-station map carries (the 96-vs-73
  neighboring stations problem). USGS needs no key at all.

SOURCES
  - USGS Water Services, Instantaneous Values (waterservices.usgs.gov) — no key.
    parameterCd 00045 = "Precipitation, total, inches" = incremental precip per
    recording interval. We sum increments over trailing windows.
  - Synoptic Data / MesoWest (api.synopticdata.com) — free tier, needs a token in
    env var SYNOPTIC_TOKEN. Aggregates the NOAA HADS + interagency RAWS + ASOS
    stations (Highlands, Franklin 1N, Coweeta/Nantahala RAWS, Brevard, ...).

HONEST CAVEATS (in the spirit of the rest of this codebase)
  - Tipping-bucket gauges UNDER-CATCH in intense or frozen precip — the same
    caveat live_rainfall.py already flags for the airport AWOS. A measured gauge
    is ground truth, not perfect truth.
  - A gauge on the approach arc tells you what fell THERE, not what the ridge over
    the watershed caught. It is a leading indicator and a bias check, not a basin
    total. Only Highlands (S) and Franklin (SW) sit close enough to inform the
    headwater basins directly.
  - Station coordinates are read LIVE from each API response (not hardcoded), so
    direction/distance are always exact and can't silently drift.
  - The parsers are written to the documented JSON schema of each service. Before
    trusting the numbers, run one live pull on a networked box and eyeball a
    station against its public dashboard (see validate_note()).

Deps: standard library only (urllib, json, datetime, math, os).
Run (networked):  SYNOPTIC_TOKEN=xxxx python gov_gauges.py
"""

import os
import json
import math
import datetime
import urllib.request
import urllib.parse

# Kept local (not imported from live_rainfall) so this module stays importable and
# unit-testable in isolation — live_rainfall pulls in sources+test_model, which a
# bare test box may not have. Trivially refactored to share later.
WATERSHED_CENTER = (35.263, -83.201)
_DIR_ORDER = {"N": 0, "NE": 1, "E": 2, "SE": 3, "S": 4, "SW": 5, "W": 6, "NW": 7}
_COMPASS8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

# Quality-control envelope. Anything outside is almost certainly a sensor fault,
# a unit mixup, or an accumulator reset — not weather.
QC_MAX_HOURLY_IN = 4.0      # >4 in in one hour anywhere near here = bad reading
QC_MAX_24H_IN = 20.0        # Helene gave ~10 in/48 h; 20 in/24 h = sensor fault
QC_STALE_HOURS = 6          # no ob in 6 h -> station is offline, don't trust it


# ---------------------------------------------------------------------------
# STATION REGISTRY — the approach arc. Only ID + network + label + role.
# Coordinates come from the live API response, so nothing here can go stale.
#   network: "usgs"     -> `id` is the USGS site number
#            "synoptic" -> `id` is the Synoptic/MesoWest STID
#   role:    "approach" -> upwind sentinel + bias check
#            "headwater"-> close enough to also inform an in-basin total
# ---------------------------------------------------------------------------
GAUGES = [
    # --- South: the Highlands–Cashiers escarpment, our wettest, first-hit sector
    {"label": "Highlands 1NW",      "network": "synoptic", "id": "HDSN7",
     "role": "headwater", "note": "Blue Ridge escarpment; catches S/SE surges first"},

    # --- Southwest: the prevailing track (Helene's path) + Coweeta research gauges
    {"label": "Raingage at Franklin","network": "usgs",    "id": "351205083213545",
     "role": "headwater", "note": "USGS real-time, no key; on the prevailing SW track"},
    {"label": "Franklin 1N",        "network": "synoptic", "id": "FNKN7",
     "role": "approach",  "note": "HADS; SW approach"},
    {"label": "Coweeta RAWS",       "network": "synoptic", "id": "CWTN7",
     "role": "approach",  "note": "USFS Nantahala Mtns; research-grade. CONFIRM STID"},

    # --- West: Nantahala / Cherokee side
    {"label": "Cow Mountain RAWS",  "network": "synoptic", "id": "COWN7",
     "role": "approach",  "note": "interagency RAWS, W/NW"},
    {"label": "Fontana Dam",        "network": "synoptic", "id": "FONN7",
     "role": "approach",  "note": "HADS; W/WNW"},

    # --- Southeast: matches the existing Lake Toxaway / Brevard sentinels
    {"label": "Brevard 2NE",        "network": "synoptic", "id": "BVDN7",
     "role": "approach",  "note": "HADS; SE approach"},
    {"label": "Guion Farm RAWS",    "network": "synoptic", "id": "GUIN7",
     "role": "approach",  "note": "interagency RAWS near Brevard, SE"},
]


# ---------------------------------------------------------------------------
# GEOMETRY (local copies — see note above)
# ---------------------------------------------------------------------------
def _haversine_km(a, b):
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = (math.sin((lat2 - lat1) / 2) ** 2 +
         math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return round(2 * R * math.asin(math.sqrt(h)))


def _bearing(a, b):
    """Initial compass bearing a -> b, degrees (0 = N, clockwise)."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _dir8(deg):
    """Bearing -> 8-point compass label."""
    return _COMPASS8[int((deg % 360) / 45.0 + 0.5) % 8]


# ---------------------------------------------------------------------------
# TIME PARSING — tolerant of the ISO variants USGS (offset) and Synoptic (Z) use.
# We normalise everything to naive UTC so trailing-window math is apples-to-apples.
# ---------------------------------------------------------------------------
def _parse_iso_utc(ts):
    """ISO-8601 (with offset, 'Z', or none) -> naive UTC datetime, or None."""
    if not ts:
        return None
    s = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        # last resort: strip to 'YYYY-MM-DDTHH:MM' and treat as UTC
        try:
            dt = datetime.datetime.strptime(s[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------------------
# PURE CORE — a list of (datetime, inches_increment) events -> trailing totals.
# One function, shared by both networks. NO network, fully unit-testable.
# ---------------------------------------------------------------------------
def trailing_totals(events, windows=(1, 3, 6, 24)):
    """events: iterable of (datetime_utc, increment_in). Returns a dict with
    h{w} trailing sums (inches) ending at the latest event, plus latest/hours.
    Increments are summed as-is (each is the precip during its interval), so this
    works for USGS 00045 (sub-hourly) and hourly HADS/RAWS alike. Returns None if
    there are no usable events."""
    ev = [(t, float(p)) for t, p in events
          if t is not None and p is not None and _is_number(p)]
    if not ev:
        return None
    ev.sort(key=lambda x: x[0])
    latest = ev[-1][0]
    out = {}
    for w in windows:
        cutoff = latest - datetime.timedelta(hours=w)
        out[f"h{w}"] = round(sum(p for t, p in ev if cutoff < t <= latest), 2)
    # also expose a peak hourly increment for QC (impossible-rate detection)
    out["peak_hourly"] = round(max((p for _, p in ev), default=0.0), 2)
    out["latest"] = latest
    out["events"] = len(ev)
    return out


def _is_number(x):
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# QUALITY CONTROL — a gauge earns its place; it is not trusted by default.
# ---------------------------------------------------------------------------
def qc_flags(totals, now=None):
    """Return a list of reject reasons for a trailing_totals dict ([] = clean)."""
    if totals is None:
        return ["no-data"]
    now = now or datetime.datetime.utcnow()
    reasons = []
    for w in (1, 3, 6, 24):
        v = totals.get(f"h{w}")
        if v is not None and v < 0:
            reasons.append(f"negative-h{w}")
    if totals.get("peak_hourly", 0.0) > QC_MAX_HOURLY_IN:
        reasons.append("impossible-rate")
    if totals.get("h24", 0.0) > QC_MAX_24H_IN:
        reasons.append("impossible-24h")
    latest = totals.get("latest")
    if latest is not None:
        age_h = (now - latest).total_seconds() / 3600.0
        if age_h > QC_STALE_HOURS:
            reasons.append(f"stale-{age_h:.0f}h")
    return reasons


# ---------------------------------------------------------------------------
# USGS Water Services (Instantaneous Values) — no API key
# ---------------------------------------------------------------------------
USGS_IV = "https://waterservices.usgs.gov/nwis/iv/"
USGS_PRECIP_PARAM = "00045"     # Precipitation, total, inches (incremental)


def usgs_iv_compute(obj):
    """Pure: USGS IV JSON -> {site_id: {name, lat, lon, h1..h24, latest, ...}}.
    Only the 00045 (precip) timeseries is read. Increments summed by window."""
    out = {}
    for ts in obj.get("value", {}).get("timeSeries", []):
        var = ts.get("variable", {})
        codes = [c.get("value") for c in var.get("variableCode", [])]
        if USGS_PRECIP_PARAM not in codes:
            continue
        si = ts.get("sourceInfo", {})
        name = si.get("siteName", "")
        site_id = next((c.get("value") for c in si.get("siteCode", [])), None)
        geo = si.get("geoLocation", {}).get("geogLocation", {})
        lat, lon = geo.get("latitude"), geo.get("longitude")
        events = []
        for block in ts.get("values", []):
            for v in block.get("value", []):
                dt = _parse_iso_utc(v.get("dateTime"))
                raw = v.get("value")
                # USGS uses -999999 as a no-data sentinel; drop it
                if raw in (None, "", "-999999", "-999999.0"):
                    continue
                events.append((dt, raw))
        totals = trailing_totals(events)
        if totals is None or site_id is None:
            continue
        out[site_id] = dict(name=name, lat=lat, lon=lon, **totals)
    return out


def usgs_fetch(sites, hours=30, timeout=30):
    """Fetch IV precip for USGS sites (list of site numbers). Returns parsed dict."""
    q = {"format": "json", "sites": ",".join(sites),
         "parameterCd": USGS_PRECIP_PARAM,
         "period": f"PT{int(hours)}H"}
    url = USGS_IV + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "cullowhee-flood/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        obj = json.load(r)
    return usgs_iv_compute(obj)


# ---------------------------------------------------------------------------
# Synoptic / MesoWest — free tier, token in env SYNOPTIC_TOKEN
# ---------------------------------------------------------------------------
SYNOPTIC_TS = "https://api.synopticdata.com/v2/stations/timeseries"


def synoptic_compute(obj):
    """Pure: Synoptic timeseries JSON -> {stid: {name, lat, lon, h1..h24, ...}}.
    Reads whichever precip_accum_one_hour_set_* variable the station carries and
    treats each value as that hour's increment."""
    out = {}
    for st in obj.get("STATION", []):
        stid = st.get("STID")
        name = st.get("NAME", "")
        lat = _to_float(st.get("LATITUDE"))
        lon = _to_float(st.get("LONGITUDE"))
        obs = st.get("OBSERVATIONS", {})
        times = obs.get("date_time", [])
        # find the hourly-precip series (name varies: ..._set_1, _set_1d, etc.)
        pkey = next((k for k in obs
                     if k.startswith("precip_accum_one_hour") or
                        k.startswith("precip_accum")), None)
        if stid is None or pkey is None or not times:
            continue
        series = obs.get(pkey, [])
        events = []
        for t, p in zip(times, series):
            events.append((_parse_iso_utc(t), p))
        totals = trailing_totals(events)
        if totals is None:
            continue
        out[stid] = dict(name=name, lat=lat, lon=lon, **totals)
    return out


def _to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def synoptic_fetch(stids, hours=30, token=None, timeout=30):
    """Fetch hourly precip timeseries for Synoptic STIDs. Token from arg or
    env SYNOPTIC_TOKEN. Returns parsed dict. Raises if no token."""
    token = token or os.environ.get("SYNOPTIC_TOKEN")
    if not token:
        raise RuntimeError("no Synoptic token (set SYNOPTIC_TOKEN or pass token=)")
    q = {"stid": ",".join(stids), "recent": int(hours) * 60,
         "vars": "precip_accum_one_hour", "units": "precip|in",
         "obtimezone": "utc", "token": token}
    url = SYNOPTIC_TS + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "cullowhee-flood/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        obj = json.load(r)
    return synoptic_compute(obj)


# ---------------------------------------------------------------------------
# ORCHESTRATION — fetch the whole arc, attach geometry + QC, one row per gauge.
# Row shape mirrors live_rainfall.airport_rainfall / upwind_compute so it drops
# straight into the existing display + ETA code.
# ---------------------------------------------------------------------------
def _row_from(parsed, meta, now=None):
    """parsed: one station's dict from *_compute. meta: its GAUGES entry."""
    lat, lon = parsed.get("lat"), parsed.get("lon")
    have_geo = lat is not None and lon is not None
    brg = _bearing(WATERSHED_CENTER, (lat, lon)) if have_geo else None
    latest = parsed.get("latest")
    flags = qc_flags(parsed, now=now)
    return dict(
        area=meta["label"], station=meta["id"], network=meta["network"],
        role=meta["role"], name=parsed.get("name", ""),
        lat=lat, lon=lon,
        dir=_dir8(brg) if brg is not None else None,
        bearing=round(brg) if brg is not None else None,
        dist_km=_haversine_km(WATERSHED_CENTER, (lat, lon)) if have_geo else None,
        h1=parsed.get("h1"), h3=parsed.get("h3"),
        h6=parsed.get("h6"), h24=parsed.get("h24"),
        latest=latest.strftime("%Y-%m-%d %H:%M UTC") if latest else None,
        source=f'{meta["network"].upper()} {meta["id"]} (logged)',
        qc="ok" if not flags else "reject:" + ",".join(flags),
        note=meta.get("note", ""),
    )


def gauge_rows(gauges=GAUGES, hours=30, token=None, now=None):
    """Fetch every registry gauge, return rows sorted clockwise from N. Network
    failures degrade gracefully: a network that can't be reached contributes no
    rows (and a `_errors` marker via the returned tuple's second element)."""
    usgs_ids = [g["id"] for g in gauges if g["network"] == "usgs"]
    syn_ids = [g["id"] for g in gauges if g["network"] == "synoptic"]
    errors = {}
    parsed = {}

    if usgs_ids:
        try:
            parsed.update({("usgs", k): v for k, v in
                           usgs_fetch(usgs_ids, hours=hours).items()})
        except Exception as e:
            errors["usgs"] = str(e)
    if syn_ids:
        try:
            parsed.update({("synoptic", k): v for k, v in
                           synoptic_fetch(syn_ids, hours=hours, token=token).items()})
        except Exception as e:
            errors["synoptic"] = str(e)

    rows = []
    for g in gauges:
        p = parsed.get((g["network"], g["id"]))
        if p is None:
            continue                      # station not returned (offline / bad id)
        rows.append(_row_from(p, g, now=now))
    rows.sort(key=lambda r: _DIR_ORDER.get(r.get("dir"), 99))
    return rows, errors


# ---------------------------------------------------------------------------
# INTEGRATION 1 — measured overlay for the arrival-ETA logic.
# Returns {dir8: best_clean_row}, so live_rainfall's steering/ETA code can prefer
# a real gauge over the modeled UPWIND_POINTS value for that direction.
# ---------------------------------------------------------------------------
def measured_upwind(rows):
    """Best CLEAN gauge per compass direction (nearest wins ties). Rejected or
    geometry-less rows are skipped — a bad gauge must never feed a warning."""
    best = {}
    for r in rows:
        if r.get("qc") != "ok" or r.get("dir") is None:
            continue
        d = r["dir"]
        if d not in best or (r.get("dist_km") or 1e9) < (best[d].get("dist_km") or 1e9):
            best[d] = r
    return best


# ---------------------------------------------------------------------------
# INTEGRATION 2 — the QPF-bias signal (the reason NOAH can leave shadow mode).
# For each clean gauge, compare its measured trailing total against the model's
# estimate for the same point. ratio > 1 => the forecast is UNDER-calling here.
# ---------------------------------------------------------------------------
def qpf_bias(rows, model_by_dir, window="h24", min_measured=0.1):
    """rows: gauge_rows() output. model_by_dir: {dir8: modeled_inches} for the
    same window (e.g. from live_rainfall.upwind_compute). Returns per-direction
    {measured, modeled, ratio, note}. ratio None when modeled≈0 or measured tiny
    (bias undefined / not raining). Only clean gauges count."""
    out = {}
    for r in rows:
        if r.get("qc") != "ok" or r.get("dir") is None:
            continue
        d = r["dir"]
        meas = r.get(window)
        modeled = model_by_dir.get(d)
        if meas is None or meas < min_measured or not modeled:
            ratio = None
        else:
            ratio = round(meas / modeled, 2)
        # keep the wettest measured gauge per direction as the representative
        if d not in out or (meas or 0) > (out[d].get("measured") or 0):
            note = ""
            if ratio is not None:
                if ratio >= 1.5:
                    note = "forecast UNDER-calling badly"
                elif ratio >= 1.15:
                    note = "forecast under-calling"
                elif ratio <= 0.67:
                    note = "forecast over-calling"
                else:
                    note = "forecast ~ on target"
            out[d] = dict(area=r["area"], measured=meas, modeled=modeled,
                          ratio=ratio, note=note)
    return out


# ---------------------------------------------------------------------------
# INTEGRATION 3 — the UPWIND OUTLOOK: turn measured upwind rain into a pre-rain
# escalation signal. This is the piece that lets the system ACT, not just show:
# when a gauge on the storm-approach arc is logging hard rain AND the steering
# flow is carrying it toward the watershed, we emit a relative risk index that
# flood_network.tiered_posture folds into its OUTLOOK tier — raising a WATCH
# before a drop falls on the ungauged headwaters.
#
# Design decisions, matched to flood_network's philosophy:
#   - Direction-gated: a gauge only counts if the storm is coming FROM it (same
#     cone test the arrival-ETA uses). Heavy rain moving AWAY is not your storm.
#   - Rain RATE -> risk, not total: a 3-inch daily total that fell overnight is
#     not the threat a 1-inch hour is. We score short-window intensity (h1/h3).
#   - Combined with noisy-OR (same combiner flood_network uses), so multiple
#     upwind gauges reinforce rather than average away.
#   - Returns a RELATIVE, UNCALIBRATED index in [0,1] — like priming/orographic.
#     It is allowed to reach WATCH and no further: measured upwind RAIN is still
#     a leading proxy for the ungauged headwaters, NOT a confirmed creek rise, so
#     it respects the same ceiling as the rest of the outlook tier.
#
# Thresholds below are TUNABLE placeholders — calibrate against the Helene
# backtest (backtest_helene.py) before trusting the absolute trip point.
# ---------------------------------------------------------------------------
UPWIND_RATE1_IN = 1.0        # 1-h total mapping to full-scale intensity risk [tunable]
UPWIND_RATE3_IN = 2.0        # 3-h total mapping to full-scale intensity risk [tunable]
UPWIND_CONE_DEG = 60         # gauge counts only if within this of the upwind heading
UPWIND_WATCH_THRESHOLD = 0.45  # combined risk that trips an Outlook WATCH [tunable]
_MPH_TO_KMH = 1.60934


def _noisy_or(probs):
    acc = 1.0
    for p in probs:
        acc *= (1.0 - max(0.0, min(1.0, p)))
    return round(1.0 - acc, 3)


def _ang_diff(a, b):
    d = abs((a - b) % 360)
    return min(d, 360 - d)


def _intensity_score(h1, h3):
    """Short-window rain intensity -> risk in [0,1]. Worst of the 1-h and 3-h
    normalised rates, so a sharp burst OR a sustained soak both register."""
    s1 = (h1 or 0.0) / UPWIND_RATE1_IN if UPWIND_RATE1_IN else 0.0
    s3 = (h3 or 0.0) / UPWIND_RATE3_IN if UPWIND_RATE3_IN else 0.0
    return max(0.0, min(1.0, max(s1, s3)))


def upwind_outlook(rows, steering, cone_deg=UPWIND_CONE_DEG,
                   watch_threshold=UPWIND_WATCH_THRESHOLD):
    """Measured upwind gauges + steering flow -> pre-rain outlook signal.

    rows: gauge_rows() output. steering: live_rainfall.steering_flow() dict
    ({from_deg, speed_mph, ...}) or None. Returns a dict:
      {risk, level, lead_min, contributors, note}
    consumable directly by flood_network.tiered_posture(..., upwind=<this>).

    Only CLEAN (qc==ok), geolocated gauges that are genuinely upwind of the
    watershed under the current steering flow contribute. If the flow is missing
    or near-calm, motion is undefined: we report the raw intensity for visibility
    but emit risk 0 (we can't claim it's heading our way)."""
    have_flow = bool(steering) and steering.get("speed_mph", 0) >= 3
    from_deg = steering.get("from_deg") if steering else None
    speed_kmh = (steering.get("speed_mph", 0) * _MPH_TO_KMH) if steering else 0.0

    contributors, scores = [], []
    for r in rows:
        if r.get("qc") != "ok" or r.get("bearing") is None:
            continue
        score = _intensity_score(r.get("h1"), r.get("h3"))
        if score <= 0:
            continue
        upwind = have_flow and _ang_diff(r["bearing"], from_deg) <= cone_deg
        eta_min = None
        if upwind and speed_kmh > 0 and r.get("dist_km") is not None:
            eta_min = max(1, round(r["dist_km"] / speed_kmh * 60))
        contributors.append(dict(
            area=r["area"], dir=r.get("dir"), h1=r.get("h1"), h3=r.get("h3"),
            score=round(score, 3), upwind=bool(upwind), eta_min=eta_min))
        if upwind:
            scores.append(score)         # only upwind gauges drive the risk

    contributors.sort(key=lambda c: (not c["upwind"], -c["score"]))
    risk = _noisy_or(scores) if scores else 0.0
    level = "WATCH" if risk >= watch_threshold else "NORMAL"
    etas = [c["eta_min"] for c in contributors if c["upwind"] and c["eta_min"]]
    lead_min = min(etas) if etas else None

    if not have_flow:
        hot = [c["area"] for c in contributors if c["score"] >= 0.5]
        note = ("Steering flow undefined (calm/variable) — cannot confirm approach; "
                + (f"heavy rain present at {', '.join(hot)} (motion unknown)."
                   if hot else "no significant upwind rain."))
    elif level == "WATCH":
        who = ", ".join(c["area"] for c in contributors if c["upwind"]
                        and c["score"] >= 0.5) or "the approach arc"
        lead = f" ~{lead_min} min out" if lead_min else ""
        note = (f"MEASURED heavy rain approaching from {who}{lead}. Leading, "
                "uncalibrated proxy for the ungauged headwaters — WATCH ceiling, "
                "not a confirmed creek rise.")
    else:
        note = "Upwind gauges below intensity threshold, or rain not tracking in."
    return dict(risk=risk, level=level, lead_min=lead_min,
                contributors=contributors, note=note)


def validate_note():
    return ("VALIDATE once on a networked box: run this file, pick one clean "
            "gauge, and compare its h24 against the same station's public page "
            "(USGS NWIS or the Synoptic/AWN dashboard). Confirm units are inches "
            "and the trailing window lines up before trusting the bias numbers.")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tok = os.environ.get("SYNOPTIC_TOKEN")
    print("Government gauge arc — MEASURED rainfall W/SW/S of Cullowhee")
    print("=" * 66)
    if not tok:
        print("(no SYNOPTIC_TOKEN set: USGS still runs; HADS/RAWS will be skipped)")
    try:
        rows, errors = gauge_rows(token=tok)
    except Exception as e:
        print(f"fetch failed entirely: {e}")
        raise SystemExit(1)

    if not rows:
        print("no gauge rows returned.")
    for r in rows:
        loc = f'{r["dir"] or "?":>2} {r["dist_km"] or "?":>3} km' \
              if r["dist_km"] is not None else "  (no geo)"
        rain = (f'1h {r["h1"]}"  3h {r["h3"]}"  6h {r["h6"]}"  24h {r["h24"]}"'
                if r["h1"] is not None else "no precip data")
        tag = "" if r["qc"] == "ok" else f'  [{r["qc"]}]'
        print(f'{r["area"]:22s} {loc}  {rain}{tag}')
        print(f'    {r["source"]}  last {r["latest"]}')

    for net, msg in (errors or {}).items():
        print(f"[{net}] unreachable: {msg}")

    print("\n" + validate_note())
