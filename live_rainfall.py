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

Run (in a networked environment):  python live_rainfall.py
Deps: standard library only (urllib, json).
"""

import os
import json
import math
import time
import datetime
import urllib.request
import urllib.parse
import test_model as tm

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
                          forecast_days=FORECAST_DAYS):
    """Map the API response to per-basin posture. Pure: no network calls."""
    out = {}
    for (bid, _), loc in zip(points.items(), data):
        daily = loc.get("daily", {})
        dates = daily.get("time", [])
        precip = daily.get("precipitation_sum", [])
        et0 = daily.get("et0_fao_evapotranspiration", []) or []
        et0_unit = loc.get("daily_units", {}).get("et0_fao_evapotranspiration", "mm")
        p5, storm, fcst_total = _split(dates, precip, forecast_days)

        today = datetime.date.today().isoformat()
        ti = dates.index(today) if today in dates else max(0, len(precip) - forecast_days)
        sm = soil_moisture_pct(precip, et0, ti, et0_unit)

        arc, res = tm.run_case(storm, p5, PRF=PRF)       # res[bid] used bid's storm/p5
        r = res[bid]
        out[bid] = dict(antecedent_5day=p5, storm=storm, forecast_total=fcst_total,
                        soil_moisture_pct=sm, arc=arc, CN=round(r["CN"]),
                        runoff=round(r["Q"], 2), peak=round(r["qp"]),
                        stage=round(r["stage"], 2), posture=r["posture"])
    return out


# ---------------------------------------------------------------------------
# CONVENIENCE: fetch + compute
# ---------------------------------------------------------------------------
def run_live(points=BASIN_POINTS, PRF=484.0):
    return compute_from_response(fetch_all(points), points, PRF)


# ---------------------------------------------------------------------------
# UPWIND RAINFALL: recent observed totals in the storm-approach corridor
# ---------------------------------------------------------------------------
def _haversine_km(a, b):
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = (math.sin((lat2 - lat1) / 2) ** 2 +
         math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return round(2 * R * math.asin(math.sqrt(h)))


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


# ---------------------------------------------------------------------------
# AMBIENT WEATHER NETWORK: real LOGGED precip from your OWN AWN station(s)
# ---------------------------------------------------------------------------
# The AWN REST API is account-scoped: the application + API keys read every device
# on YOUR account. We pull them, keep the ones near the watershed, and report
# trailing rain — 1 h and 24 h come straight from AWN's own fields (hourlyrainin,
# 24hourrainin); 3 h / 6 h are integrated from the 5-min history. Keys come from the
# environment or are passed in from Streamlit secrets — NEVER hardcode them; the
# repo is public. Regenerate keys at ambientweather.net/account if they ever leak.
AMBIENT_API = "https://api.ambientweather.net/v1"
AMBIENT_NEAR_KM = 40        # ignore account devices farther than this from the watershed
_RAIN_KEYS = ("hourlyrainin", "dailyrainin", "24hourrainin")


def _ambient_get(path, params, timeout):
    url = AMBIENT_API + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "cullowhee-flood/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _ambient_trailing(records):
    """3-h / 6-h trailing totals (in) integrated from an AWN device's 5-min history
    via dailyrainin deltas (handles the local-midnight reset); also returns 1-h/24-h
    as fallbacks. None if the station logs no dailyrainin (i.e. has no rain gauge)."""
    recs = sorted((r for r in records if r.get("dateutc") is not None),
                  key=lambda r: r["dateutc"])
    incs, prev = [], None
    for r in recs:
        d = r.get("dailyrainin")
        if d is None:
            continue
        inc = 0.0 if prev is None else (d if d < prev else d - prev)   # reset-aware
        incs.append((r["dateutc"], max(0.0, inc)))
        prev = d
    if not incs:
        return None
    latest = incs[-1][0]

    def trail(hours):
        cut = latest - hours * 3600 * 1000
        return round(sum(i for t, i in incs if t > cut), 2)

    now_ms = datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000
    return dict(h1=trail(1), h3=trail(3), h6=trail(6), h24=trail(24),
                age_min=round((now_ms - latest) / 60000))


def ambient_rainfall(app_key=None, api_key=None, near_km=AMBIENT_NEAR_KM, timeout=30):
    """Real logged precip from AWN stations on your account, within near_km of the
    watershed. Returns {"rows": [...], "status": {...}} — status carries diagnostics
    (configured?, device count, nearby stations, any error) so the app can show why
    rows did or didn't appear. Key precedence: arg > env."""
    app_key = app_key or os.environ.get("AMBIENT_APP_KEY")
    api_key = api_key or os.environ.get("AMBIENT_API_KEY")
    status = {"configured": bool(app_key and api_key), "near_km": near_km,
              "devices": 0, "near": [], "error": None}
    if not status["configured"]:
        return {"rows": [], "status": status}

    base = {"applicationKey": app_key, "apiKey": api_key}
    try:
        devices = _ambient_get("/devices", base, timeout)
    except Exception as e:
        status["error"] = str(e)[:160]
        return {"rows": [], "status": status}
    if not isinstance(devices, list):
        status["error"] = "unexpected /devices response (check that both keys are valid)"
        return {"rows": [], "status": status}
    status["devices"] = len(devices)

    rows = []
    for dev in devices:
        info = dev.get("info", {}) or {}
        coords = ((info.get("coords") or {}).get("coords")) or {}
        lat, lon = coords.get("lat"), coords.get("lon")
        if lat is None or lon is None:
            continue
        dist = _haversine_km(WATERSHED_CENTER, (lat, lon))
        if dist > near_km:
            continue
        last = dev.get("lastData", {}) or {}
        has_rain = any(k in last for k in _RAIN_KEYS)
        name = (info.get("name") or "AWN station").strip()
        status["near"].append({"name": name, "dist_km": dist, "rain": has_rain})
        if not has_rain:                                # no rain gauge -> no row
            continue
        mac = dev.get("macAddress")
        if not mac:
            continue
        time.sleep(1.1)                                 # respect AWN's 1 req/sec/apiKey
        try:
            hist = _ambient_get("/devices/" + urllib.parse.quote(mac),
                                dict(base, limit=288), timeout)   # ~24 h of 5-min recs
        except Exception:
            hist = []
        tr = _ambient_trailing(hist)
        h1 = last.get("hourlyrainin")                   # AWN's own rolling 1-h total
        h24 = last.get("24hourrainin")                  # AWN's own trailing 24-h total
        if tr:
            h1 = h1 if h1 is not None else tr["h1"]
            h24 = h24 if h24 is not None else tr["h24"]
            h3, h6, age = tr["h3"], tr["h6"], tr["age_min"]
        else:
            h3 = h6 = age = None
        rows.append(dict(area=name, dir="AWN", dist_km=dist,
                         h1=round(h1, 2) if h1 is not None else None, h3=h3, h6=h6,
                         h24=round(h24, 2) if h24 is not None else None,
                         age_min=age, source="AWN (logged)"))
    rows.sort(key=lambda r: r["dist_km"])
    return {"rows": rows, "status": status}


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

    print(f"\nAmbient Weather Network — your account stations within {AMBIENT_NEAR_KM} km "
          "(REAL logged precip):")
    try:
        res = ambient_rainfall()        # reads AMBIENT_APP_KEY / AMBIENT_API_KEY from env
        st_, arows = res["status"], res["rows"]
        if not st_["configured"]:
            print("  not configured — set AMBIENT_APP_KEY + AMBIENT_API_KEY in env")
        elif st_["error"]:
            print(f"  API error: {st_['error']}")
        else:
            print(f"  account devices: {st_['devices']}  |  within range: "
                  f"{len(st_['near'])}")
            for n in sorted(st_["near"], key=lambda x: x["dist_km"]):
                print(f"    - {n['name'][:24]:24s} {n['dist_km']:>2} km  "
                      f"{'rain gauge' if n['rain'] else 'NO rain gauge'}")
        for a in arows:
            def _s(v):
                return f"{v:5.2f}" if isinstance(v, (int, float)) else "  n/a"
            age = f"{a['age_min']}m ago" if a["age_min"] is not None else "age n/a"
            print(f"  {a['area'][:22]:22s} ({a['dist_km']:>2} km)  1h {_s(a['h1'])}  "
                  f"3h {_s(a['h3'])}  6h {_s(a['h6'])}  24h {_s(a['h24'])}  ({age})")
    except Exception as e:
        print(f"  AWN fetch failed (need network + valid keys): {e}")
