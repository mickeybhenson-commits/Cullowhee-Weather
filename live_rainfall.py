"""
live_rainfall.py  —  REAL rainfall in, no sensors: drives the flood engine from
live forecast + modeled antecedent soil state.

Source: Open-Meteo (free, no API key). ONE call returns, per basin point:
  - the trailing `past_days` of daily rainfall  -> antecedent / soil-moisture state
  - the next `forecast_days` of daily rainfall   -> the upcoming storm
both in inches. Those feed test_model.run_case exactly like the synthetic storms,
so the posture map can run on real weather.

CAVEAT (the honest one): any forecast — Open-Meteo, GFS, even NWS QPF —
under-calls orographic mountain rainfall. Modeling antecedent wetness sharpens
how much of the rain runs off; it cannot recover rain the forecast never saw.
So this is a SHADOW-MODE system: real, live, good for validation — not a basis
for public warnings until the gauges are catching the QPF bias in real time.

MEASURED UPWIND (new): gov_gauges.py adds public, quality-controlled government
rain gauges on the W/SW/S storm-approach arc (USGS Franklin, HADS Highlands /
Brevard, Nantahala RAWS). When present they OVERLAY the modeled upwind corridor
below, so the arrival-ETA readout runs on real logged rain, and qpf_bias() reports
how much the forecast is under-calling RIGHT NOW — the number that lets this
system climb out of shadow mode. gov_gauges is optional: if it or its Synoptic
token is missing, everything here still runs on the model alone.

Run (in a networked environment):  python live_rainfall.py
  (optional, for HADS/RAWS gauges:  SYNOPTIC_TOKEN=xxxx python live_rainfall.py)
Deps: standard library only (urllib, json).
"""

import os
import json
import math
import datetime
import urllib.request
import urllib.parse
import test_model as tm
import sources as src

# Measured government gauges on the approach arc. Optional: keep live_rainfall
# runnable even if the module isn't deployed yet.
try:
    import gov_gauges as gov
except Exception:
    gov = None

# Basin representative points (lat, lon). Centroids/pour points of each sub-basin.
BASIN_POINTS = {
    "CC-UP-503":    (35.241, -83.185),
    "CC-MS-1100":   (35.265, -83.190),
    "CC-TIL-705":   (35.268, -83.205),
    "CC-SPD-1830":  (35.270, -83.190),
    "CC-COX-097":   (35.302, -83.178),
    "CC-LB-171":    (35.305, -83.195),
    "CC-WCU-2260":  (35.290, -83.185),
    "CC-MOUTH-2340":(35.300, -83.185),
}

# Ring of sentinel points in all 8 directions around the watershed. Whichever
# direction shows recent rain is where weather is approaching from — so this works
# for ANY approach (east, north, etc.), not just the prevailing SW/W track. Each
# is a recognizable WNC town ~20-60 km out, giving some lead time.
WATERSHED_CENTER = (35.263, -83.201)
UPWIND_POINTS = {
    "Maggie Valley": (35.52, -83.10, "N"),
    "Waynesville":   (35.49, -82.99, "NE"),
    "Brevard":       (35.23, -82.73, "E"),
    "Lake Toxaway":  (35.13, -82.93, "SE"),
    "Highlands":     (35.05, -83.20, "S"),
    "Franklin":      (35.18, -83.38, "SW"),
    "Andrews":       (35.20, -83.83, "W"),
    "Bryson City":   (35.43, -83.45, "NW"),
}
_DIR_ORDER = {"N": 0, "NE": 1, "E": 2, "SE": 3, "S": 4, "SW": 5, "W": 6, "NW": 7}

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
PAST_DAYS = 30          # longer history so the soil-moisture bucket can spin up
FORECAST_DAYS = 3
SOIL_CAPACITY_IN = 4.0  # assumed plant-available water over the root zone (until probes)


# ---------------------------------------------------------------------------
# NETWORK: fetch daily rainfall + reference ET for all basins in ONE bulk call
# ---------------------------------------------------------------------------
def fetch_all(points=BASIN_POINTS, past_days=PAST_DAYS, forecast_days=FORECAST_DAYS,
              timeout=30):
    lats = ",".join(f"{p[0]}" for p in points.values())
    lons = ",".join(f"{p[1]}" for p in points.values())
    q = {
        "latitude": lats, "longitude": lons,
        "daily": "precipitation_sum,et0_fao_evapotranspiration",
        "past_days": past_days, "forecast_days": forecast_days,
        "precipitation_unit": "inch",
        "timezone": "America/New_York",
    }
    url = OPEN_METEO + "?" + urllib.parse.urlencode(q, safe=",")
    req = urllib.request.Request(url, headers={"User-Agent": "cullowhee-flood/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return data if isinstance(data, list) else [data]   # bulk -> list; single -> dict


# ---------------------------------------------------------------------------
# PURE LOGIC (no network — unit-testable): response -> per-basin postures
# ---------------------------------------------------------------------------
def _split(dates, precip, forecast_days=FORECAST_DAYS):
    """Return (antecedent_5day_in, storm_in, fcst_total_in) from a daily series."""
    precip = [(v if v is not None else 0.0) for v in precip]
    today = datetime.date.today().isoformat()
    ti = dates.index(today) if today in dates else max(0, len(precip) - forecast_days)
    p5 = sum(precip[max(0, ti - 5):ti])                 # 5 completed days before today
    fwin = precip[ti:ti + forecast_days]                # today + forecast window
    storm = max(fwin) if fwin else 0.0                  # worst upcoming 24-hr day
    return round(p5, 2), round(storm, 2), round(sum(fwin), 2)


def soil_moisture_pct(precip, et0, end_idx, et0_unit="mm",
                      cap=SOIL_CAPACITY_IN, init_frac=0.5):
    """Single-layer water-balance bucket -> % of available capacity (0-100),
    forced by real daily precip and reference ET (ET is water-limited as the
    bucket dries). MODELED, not a sensor reading: the absolute level depends on
    the assumed capacity, so the day-to-day trend is the trustworthy signal.
    To be replaced by NWM soil moisture, then by deployed probes."""
    to_in = (1.0 / 25.4) if str(et0_unit).startswith("mm") else 1.0
    s = init_frac * cap
    n = min(end_idx, len(precip) - 1)
    for t in range(0, n + 1):
        p = precip[t] if (t < len(precip) and precip[t] is not None) else 0.0
        if t < len(et0) and et0[t] is not None:
            e = et0[t] * to_in            # reference ET, converted to inches
        else:
            e = 0.1                       # in/day fallback if ET missing
        e_act = e * min(1.0, s / cap)     # actual ET limited by available water
        s = max(0.0, min(cap, s + p - e_act))
    return round(s / cap * 100)


def compute_from_response(data, points=BASIN_POINTS, PRF=484.0,
                          forecast_days=FORECAST_DAYS, now=None,
                          storm_correction=None):
    """Map the API response to per-basin posture. Pure: no network calls.

    `storm_correction` (optional): {basin_id: factor} from
    gov_sources.storm_correction_map() — an UPWARD-ONLY (>= 1.0) multiplier that
    scales a basin's modeled storm rain up toward measured reality when the
    government-gauge arc shows the forecast under-calling on that basin's inflow
    direction. It is applied to the MODELED value BEFORE source resolution, so a
    real in-basin sensor still out-ranks it. Injected (not fetched) to keep this
    function pure. None => unchanged behavior.

    Every sensor-replaceable quantity (storm rain, antecedent rain, soil
    moisture, stage) is routed through sources.resolve(): today it returns the
    MODELED value, but once a sensor feeds Firestore for a basin, that value
    flips to MEASURED automatically (gated by freshness + range). Stage is
    special -- a real stage sensor is the most direct truth, so when MEASURED it
    drives the posture directly instead of the rating-derived stage. Each row
    carries provenance tags (src_*) so the UI can badge measured vs modeled."""
    out = {}
    for (bid, _), loc in zip(points.items(), data):
        daily = loc.get("daily", {})
        dates = daily.get("time", [])
        precip = daily.get("precipitation_sum", [])
        et0 = daily.get("et0_fao_evapotranspiration", []) or []
        et0_unit = loc.get("daily_units", {}).get("et0_fao_evapotranspiration", "mm")
        m_p5, m_storm, fcst_total = _split(dates, precip, forecast_days)

        # upward-only QPF-bias correction from the measured gov-gauge arc, if any
        if storm_correction:
            m_storm = round(m_storm * max(1.0, storm_correction.get(bid, 1.0)), 2)

        today = datetime.date.today().isoformat()
        ti = dates.index(today) if today in dates else max(0, len(precip) - forecast_days)
        m_sm = soil_moisture_pct(precip, et0, ti, et0_unit)

        # --- source resolution: sensor replaces model when present/fresh/in-range
        rain = src.resolve(src.Q_RAIN_STORM, bid, m_storm, now=now)
        ant = src.resolve(src.Q_RAIN_5DAY, bid, m_p5, now=now)
        soil = src.resolve(src.Q_SOIL, bid, m_sm, now=now)

        # engine runs on whatever rainfall won (measured gauge or model)
        arc, res = tm.run_case(rain.value, ant.value, PRF=PRF)
        r = res[bid]
        m_stage = round(r["stage"], 2)

        # stage sensor (NOAH) is the most direct truth: if MEASURED it governs
        stage = src.resolve(src.Q_STAGE, bid, m_stage, now=now)
        b = tm.BASINS[bid]
        posture = (tm.posture(stage.value, b, bid)
                   if stage.tier == src.MEASURED else r["posture"])

        out[bid] = dict(
            antecedent_5day=ant.value, storm=rain.value, forecast_total=fcst_total,
            soil_moisture_pct=soil.value, arc=arc, CN=round(r["CN"]),
            runoff=round(r["Q"], 2), peak=round(r["qp"]),
            stage=stage.value, posture=posture,
            # provenance for UI badges + the monitoring/forecasting split
            src_rain=rain.tier, src_rain_name=rain.source, src_rain_note=rain.note,
            src_ant=ant.tier, src_soil=soil.tier,
            src_stage=stage.tier, src_stage_name=stage.source, src_stage_note=stage.note,
        )
    return out


# ---------------------------------------------------------------------------
# CONVENIENCE: fetch + compute
# ---------------------------------------------------------------------------
def run_live(points=BASIN_POINTS, PRF=484.0, basin_inflow=None):
    """Fetch + compute. If `basin_inflow` ({basin_id: compass_dir}) is given and
    gov_gauges is available, apply the upward-only QPF-bias correction from the
    measured approach-arc gauges. Any failure (no module, no token, no network)
    degrades silently to the uncorrected model."""
    correction = None
    if basin_inflow and gov is not None:
        try:
            import gov_sources
            grows, _err = gov.gauge_rows(token=os.environ.get("SYNOPTIC_TOKEN"))
            correction = gov_sources.storm_correction_map(
                basin_inflow, grows, upwind_rainfall())
        except Exception:
            correction = None
    return compute_from_response(fetch_all(points), points, PRF,
                                 storm_correction=correction)


# ---------------------------------------------------------------------------
# UPWIND RAINFALL: recent observed totals in the storm-approach corridor
# ---------------------------------------------------------------------------
def _haversine_km(a, b):
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = (math.sin((lat2 - lat1) / 2) ** 2 +
         math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return round(2 * R * math.asin(math.sqrt(h)))


def _bearing(a, b):
    """Initial compass bearing from a to b in degrees (0 = N, clockwise)."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _current_hour_index(times, now):
    ci = None
    for i, t in enumerate(times):
        try:
            dt = datetime.datetime.fromisoformat(t)
        except Exception:
            continue
        if dt <= now:
            ci = i
        else:
            break
    return ci


def upwind_compute(data, points=UPWIND_POINTS):
    """Pure: API response -> recent-rainfall rows per upwind area. No network."""
    out = []
    now = datetime.datetime.now()
    for (name, val), loc in zip(points.items(), data):
        lat, lon, dirn = val
        hourly = loc.get("hourly", {})
        times = hourly.get("time", [])
        pr = [(x if x is not None else 0.0) for x in hourly.get("precipitation", [])]
        ci = _current_hour_index(times, now)

        def trail(h):
            if ci is None:
                return 0.0
            return round(sum(pr[max(0, ci - h + 1):ci + 1]), 2)

        out.append(dict(area=name, dir=dirn,
                        dist_km=_haversine_km(WATERSHED_CENTER, (lat, lon)),
                        bearing=round(_bearing(WATERSHED_CENTER, (lat, lon))),
                        h1=trail(1), h3=trail(3), h6=trail(6), h24=trail(24)))
    out.sort(key=lambda r: _DIR_ORDER.get(r["dir"], 99))   # clockwise from north
    return out


def upwind_rainfall(points=UPWIND_POINTS, timeout=30):
    lats = ",".join(f"{v[0]}" for v in points.values())
    lons = ",".join(f"{v[1]}" for v in points.values())
    q = {"latitude": lats, "longitude": lons, "hourly": "precipitation",
         "past_days": 2, "forecast_days": 1, "precipitation_unit": "inch",
         "timezone": "America/New_York"}
    url = OPEN_METEO + "?" + urllib.parse.urlencode(q, safe=",")
    req = urllib.request.Request(url, headers={"User-Agent": "cullowhee-flood/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    data = data if isinstance(data, list) else [data]
    return upwind_compute(data, points)


# ---------------------------------------------------------------------------
# STEERING FLOW: storm-motion proxy from the ~700 mb wind, and per-town arrival
# ETA. MODELED, not a tracked storm: storms broadly follow the mid-level steering
# wind, but in these mountains terrain channels the low-level flow and convection
# can propagate off the steering vector — so treat ETAs as planning estimates.
# ---------------------------------------------------------------------------
STEERING_LEVEL = "700hPa"     # ~3 km; classic single-level storm-steering proxy
UPWIND_CONE_DEG = 60          # a town gets an ETA only if within this of the upwind dir
_COMPASS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _compass(deg):
    return _COMPASS16[int((deg % 360) / 22.5 + 0.5) % 16]


def _ang_diff(a, b):
    d = abs((a - b) % 360)
    return min(d, 360 - d)


def steering_flow(center=WATERSHED_CENTER, level=STEERING_LEVEL, timeout=30):
    """Storm-motion proxy from the steering wind over the watershed. Returns
    {from_deg, from_compass, toward_deg, toward_compass, speed_mph} or None.
    Wind direction is meteorological (the direction it blows FROM); storms move
    toward the opposite heading. MODELED (Open-Meteo), not a tracked storm."""
    q = {"latitude": center[0], "longitude": center[1],
         "hourly": f"wind_speed_{level},wind_direction_{level}",
         "wind_speed_unit": "mph", "timezone": "America/New_York", "forecast_days": 1}
    url = OPEN_METEO + "?" + urllib.parse.urlencode(q, safe=",")
    req = urllib.request.Request(url, headers={"User-Agent": "cullowhee-flood/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    spd = hourly.get(f"wind_speed_{level}", [])
    drc = hourly.get(f"wind_direction_{level}", [])
    ci = _current_hour_index(times, datetime.datetime.now())
    if ci is None or ci >= len(spd) or ci >= len(drc):
        return None
    speed, frm = spd[ci], drc[ci]
    if speed is None or frm is None:
        return None
    toward = (frm + 180) % 360
    return dict(from_deg=round(frm), from_compass=_compass(frm),
                toward_deg=round(toward), toward_compass=_compass(toward),
                speed_mph=round(speed))


def arrival_eta(steering, bearing, dist_km, cone_deg=UPWIND_CONE_DEG):
    """Minutes until rain over an upwind point reaches the watershed center, given
    the steering flow. None if the point isn't upwind (storm isn't coming from there)
    or the flow is too weak for motion to be defined. A MODELED planning estimate."""
    if not steering or steering["speed_mph"] < 3:      # near-calm -> motion ill-defined
        return None
    if _ang_diff(bearing, steering["from_deg"]) > cone_deg:
        return None                                    # point isn't upwind
    speed_kmh = steering["speed_mph"] * 1.60934
    return max(1, round(dist_km / speed_kmh * 60))


# ---------------------------------------------------------------------------
# MEASURED UPWIND OVERLAY: fold real government gauges into the corridor.
# Returns (measured_by_dir, gauge_rows, errors). measured_by_dir maps an 8-point
# compass direction to the best CLEAN gauge row for that direction, so callers can
# prefer a logged total over the modeled UPWIND_POINTS value. Fully optional and
# defensive: no gov_gauges module, no token, or a network failure all degrade to
# ({}, [], {...}) and the model-only path keeps working.
# ---------------------------------------------------------------------------
def measured_upwind_overlay(hours=30, token=None):
    if gov is None:
        return {}, [], {"gov_gauges": "module not importable"}
    token = token or os.environ.get("SYNOPTIC_TOKEN")
    try:
        rows, errors = gov.gauge_rows(hours=hours, token=token)
    except Exception as e:
        return {}, [], {"gov_gauges": str(e)}
    return gov.measured_upwind(rows), rows, errors


def upwind_qpf_bias(modeled_rows, gauge_rows, window="h24"):
    """Per-direction measured-vs-modeled comparison — the live under-call signal.
    modeled_rows: upwind_compute() output. gauge_rows: measured_upwind_overlay()'s
    second return. Empty dict if gov_gauges is unavailable."""
    if gov is None or not gauge_rows:
        return {}
    model_by_dir = {r["dir"]: r.get(window) for r in modeled_rows}
    return gov.qpf_bias(gauge_rows, model_by_dir, window=window)


# ---------------------------------------------------------------------------
# LOCAL GAUGE: real LOGGED precip from the Jackson County Airport AWOS (K24A)
# ---------------------------------------------------------------------------
# This is the one MEASURED rainfall point in the system: an actual heated-gauge
# AWOS-III at the airport (~6 km N, right by the Body Farm gateway site), not a
# model. Source = Iowa Environmental Mesonet's ASOS archive (same service as the
# radar tiles), field `p01i` = logged precip in the prior hour, in inches. We sum
# it over trailing windows to match the approach-rainfall columns.
# Honest caveat: a single AWOS tipping bucket can under-catch in heavy/frozen
# precip and occasionally drop hours — ground truth, but not infallible.
IEM_ASOS = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
AIRPORT = {"id": "24A", "name": "Jackson Co. Airport", "lat": 35.3172, "lon": -83.2097}


def airport_compute(csv_text):
    """Pure: IEM asos.py CSV (station,valid,p01i) -> trailing rainfall totals.
    p01i is the logged precip in the hour before each ob; we keep one value per
    clock hour (max) and sum trailing windows. Returns None if no usable rows."""
    by_hour, latest = {}, None
    for line in csv_text.splitlines():
        parts = line.split(",")
        if len(parts) < 3:
            continue
        ts = parts[1].strip()
        try:                                   # skips header ('valid') + '#' lines
            dt = datetime.datetime.strptime(ts[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        pval = parts[2].strip()
        if pval in ("M", "", "None", "null"):  # missing — skip (don't count as 0)
            continue
        p = 0.0001 if pval == "T" else None    # trace
        if p is None:
            try:
                p = float(pval)
            except ValueError:
                continue
        hk = dt.replace(minute=0, second=0, microsecond=0)
        by_hour[hk] = max(by_hour.get(hk, 0.0), p)   # one (max) value per hour
        latest = dt if latest is None or dt > latest else latest
    if not by_hour:
        return None
    end_hour = latest.replace(minute=0, second=0, microsecond=0)

    def trail(n):
        return round(sum(by_hour.get(end_hour - datetime.timedelta(hours=k), 0.0)
                         for k in range(n)), 2)

    return dict(h1=trail(1), h3=trail(3), h6=trail(6), h24=trail(24),
                latest=latest.strftime("%I:%M %p").lstrip("0"),
                hours_logged=len(by_hour))


def airport_rainfall(station="24A", hours=30, timeout=30):
    """Fetch + compute logged precip totals for the airport AWOS. Returns a row
    dict (area/dir/dist_km/h1/h3/h6/h24/latest/source) or None if unavailable."""
    q = {"station": station, "data": "p01i", "hours": hours,
         "tz": "America/New_York", "format": "onlycomma",
         "missing": "M", "trace": "0.0001", "latlon": "no"}
    url = IEM_ASOS + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "cullowhee-flood/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        txt = r.read().decode("utf-8", "replace")
    res = airport_compute(txt)
    if res is None:
        return None
    res.update(area=AIRPORT["name"], dir="local", station=f"K{station}",
               dist_km=_haversine_km(WATERSHED_CENTER, (AIRPORT["lat"], AIRPORT["lon"])),
               source=f"AWOS K{station} (logged)")
    return res


if __name__ == "__main__":
    try:
        results = run_live()
    except Exception as e:
        print(f"Fetch failed (need network access to api.open-meteo.com): {e}")
        raise SystemExit(1)
    print(f"{'basin':14s} {'ante_5d':>7} {'soil%':>6} {'storm':>6} {'fcst3d':>6} "
          f"{'arc':>4} {'depth':>6}  posture")
    for bid, r in results.items():
        print(f"{bid:14s} {r['antecedent_5day']:7.2f} {r['soil_moisture_pct']:5d}% "
              f"{r['storm']:6.2f} {r['forecast_total']:6.2f} {r['arc']:>4} "
              f"{r['stage']:6.2f}  {r['posture']}")

    print("\nSteering flow (storm-motion proxy, 700 mb wind — MODELED):")
    # Pull the modeled upwind corridor once so we can both (a) show ETAs and
    # (b) compare it against the measured government gauges for the QPF bias.
    modeled_rows = []
    try:
        modeled_rows = upwind_rainfall()
    except Exception as e:
        print(f"  upwind fetch failed: {e}")

    # Measured overlay from the government-gauge arc (optional; needs SYNOPTIC_TOKEN
    # for the HADS/RAWS stations, but the USGS Franklin gage works with no key).
    meas_by_dir, gauge_rows, gov_err = measured_upwind_overlay()

    try:
        sf = steering_flow()
        if sf is None:
            print("  unavailable (no 700 mb wind in the model response)")
        elif sf["speed_mph"] < 3:
            print(f"  light / variable ({sf['speed_mph']} mph) — motion ill-defined, "
                  "no ETAs")
        else:
            print(f"  from {sf['from_compass']} at {sf['speed_mph']} mph -> storms "
                  f"tracking {sf['toward_compass']}")
            for r in modeled_rows:
                eta = arrival_eta(sf, r["bearing"], r["dist_km"])
                if eta is None:
                    continue
                g = meas_by_dir.get(r["dir"])
                if g is not None and g.get("h24") is not None:
                    # MEASURED beats MODELED: show the real gauge's logged total
                    src_tag = (f'gauge {g["area"]}: 24h {g["h24"]}" '
                               f'MEASURED ({g["dist_km"]} km {g["dir"]})')
                else:
                    src_tag = f'model 24h {r["h24"]}"'
                print(f"    {r['area']:14s} ({r['dir']:>2}) ~{eta} min out  ·  {src_tag}")
    except Exception as e:
        print(f"  steering fetch failed: {e}")

    # QPF bias: how much is the forecast under-calling on the approach arc RIGHT NOW?
    bias = upwind_qpf_bias(modeled_rows, gauge_rows)
    if bias:
        print("\nQPF bias — MEASURED gov gauge vs MODEL (24 h), approach arc:")
        for d in ("S", "SW", "W", "SE", "E", "NE", "N", "NW"):
            if d not in bias:
                continue
            bd = bias[d]
            ratio = f'{bd["ratio"]:.2f}x' if bd["ratio"] is not None else "  n/a"
            print(f'  {d:>2}  {bd["area"]:22s} meas {bd["measured"]}"  '
                  f'model {bd["modeled"]}"  = {ratio}  {bd["note"]}')

    print("\nMeasured government gauge arc (REAL logged precip, W/SW/S of Cullowhee):")
    if gov is None:
        print("  gov_gauges.py not importable — model-only mode.")
    elif not gauge_rows:
        print("  no gauge rows returned "
              f"({gov_err or 'stations offline or SYNOPTIC_TOKEN unset'}).")
    else:
        for r in gauge_rows:
            if r.get("h1") is None:
                continue
            loc = (f'{r["dir"] or "?":>2} {r["dist_km"]:>3} km'
                   if r.get("dist_km") is not None else "  (no geo)")
            tag = "" if r["qc"] == "ok" else f'  [{r["qc"]}]'
            print(f'  {r["area"]:22s} {loc}  '
                  f'1h {r["h1"]}"  3h {r["h3"]}"  6h {r["h6"]}"  24h {r["h24"]}"{tag}')
        for net, msg in (gov_err or {}).items():
            print(f"  [{net}] {msg}")

    print("\nLocal airport gauge (REAL logged precip, IEM/AWOS K24A):")
    try:
        ap = airport_rainfall()
        if ap:
            print(f"  {ap['area']} ({ap['station']}, "
                  f"{round(ap['dist_km']*0.621371,1)} mi) — "
                  f"1h {ap['h1']}\"  3h {ap['h3']}\"  6h {ap['h6']}\"  24h {ap['h24']}\"  "
                  f"(last ob {ap['latest']}, {ap['hours_logged']} hrs logged)")
        else:
            print("  no usable precip rows returned (sensor gap or station offline)")
    except Exception as e:
        print(f"  airport gauge fetch failed (need network to mesonet.agron.iastate.edu): {e}")
