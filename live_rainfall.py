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

import json
import math
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
