import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="WCU Belk Weather Intelligence",
    layout="wide",
)

st_autorefresh(interval=300000, key="refresh")

# ============================================================
# CONSTANTS / CONFIG
# ============================================================

LAT = 35.307205
LON = -83.182899
SITE = "Belk Building — Western Carolina University, Cullowhee, NC"
TIMEZONE = ZoneInfo("America/New_York")

AMBIENT_API_KEY = st.secrets.get(
    "AMBIENT_API_KEY",
    "9ed066cb260c42adbe8778e0afb09e747f8450a7dd20479791a18d692b722334",
)
AMBIENT_APP_KEY = st.secrets.get(
    "AMBIENT_APP_KEY",
    "9ed066cb260c42adbe8778e0afb09e747f8450a7dd20479791a18d692b722334",
)

AMBIENT_DEVICE_MAC = "35c7b0accb75a84d7891d82f125001a8"
AIRPORT_ID = "K24A"

USGS_GAUGES = {
    "03439000": "Tuckasegee @ Cullowhee",
    "03460000": "Tuckasegee @ Bryson City",
}

MODEL_PARAMS = {
    "hrrr": "hrrr_conus",
    "gfs": "gfs_seamless",
}

COLORS = {
    "cyan": "#00FFFF",
    "blue": "#5AC8FA",
    "green": "#00FF9C",
    "lime": "#AAFF00",
    "yellow": "#FFD700",
    "orange": "#FF8C00",
    "red": "#FF3333",
    "white": "#FFFFFF",
    "muted": "#7AACCC",
}

GAUGE_THRESHOLDS = {
    "lightning": [
        {"range": [0, 1], "color": "rgba(0,255,156,0.12)"},
        {"range": [1, 5], "color": "rgba(255,51,51,0.12)"},
        {"range": [5, 10], "color": "rgba(255,140,0,0.12)"},
        {"range": [10, 15], "color": "rgba(255,215,0,0.12)"},
        {"range": [15, 25], "color": "rgba(0,255,156,0.12)"},
    ],
    "temp": [
        {"range": [0, 32], "color": "rgba(90,200,250,0.12)"},
        {"range": [32, 50], "color": "rgba(0,255,255,0.12)"},
        {"range": [50, 80], "color": "rgba(0,255,156,0.12)"},
        {"range": [80, 95], "color": "rgba(255,215,0,0.12)"},
        {"range": [95, 105], "color": "rgba(255,140,0,0.12)"},
        {"range": [105, 120], "color": "rgba(255,51,51,0.12)"},
    ],
    "uv": [
        {"range": [0, 3], "color": "rgba(0,255,156,0.12)"},
        {"range": [3, 6], "color": "rgba(255,215,0,0.12)"},
        {"range": [6, 8], "color": "rgba(255,140,0,0.12)"},
        {"range": [8, 12], "color": "rgba(255,51,51,0.12)"},
    ],
    "humidity": [
        {"range": [0, 30], "color": "rgba(90,200,250,0.12)"},
        {"range": [30, 60], "color": "rgba(0,255,156,0.12)"},
        {"range": [60, 80], "color": "rgba(255,215,0,0.12)"},
        {"range": [80, 100], "color": "rgba(255,140,0,0.12)"},
    ],
    "wind": [
        {"range": [0, 15], "color": "rgba(0,255,156,0.12)"},
        {"range": [15, 25], "color": "rgba(255,215,0,0.12)"},
        {"range": [25, 35], "color": "rgba(255,140,0,0.12)"},
        {"range": [35, 60], "color": "rgba(255,51,51,0.12)"},
    ],
    "soil": [
        {"range": [0, 25], "color": "rgba(90,200,250,0.12)"},
        {"range": [25, 50], "color": "rgba(0,255,156,0.12)"},
        {"range": [50, 75], "color": "rgba(255,215,0,0.12)"},
        {"range": [75, 100], "color": "rgba(255,51,51,0.12)"},
    ],
    "precip_prob": [
        {"range": [0, 20], "color": "rgba(0,255,156,0.12)"},
        {"range": [20, 50], "color": "rgba(255,215,0,0.12)"},
        {"range": [50, 80], "color": "rgba(255,140,0,0.12)"},
        {"range": [80, 100], "color": "rgba(255,51,51,0.12)"},
    ],
    "freeze": [
        {"range": [0, 28], "color": "rgba(255,51,51,0.12)"},
        {"range": [28, 32], "color": "rgba(255,140,0,0.12)"},
        {"range": [32, 45], "color": "rgba(255,215,0,0.12)"},
        {"range": [45, 80], "color": "rgba(0,255,156,0.12)"},
    ],
    "fog": [
        {"range": [0, 3], "color": "rgba(255,51,51,0.12)"},
        {"range": [3, 9], "color": "rgba(255,140,0,0.12)"},
        {"range": [9, 18], "color": "rgba(255,215,0,0.12)"},
        {"range": [18, 30], "color": "rgba(0,255,156,0.12)"},
    ],
    "aqi": [
        {"range": [0, 50], "color": "rgba(0,255,156,0.12)"},
        {"range": [50, 100], "color": "rgba(170,255,0,0.12)"},
        {"range": [100, 150], "color": "rgba(255,215,0,0.12)"},
        {"range": [150, 200], "color": "rgba(255,140,0,0.12)"},
    ],
    "rain3d": [
        {"range": [0, 0.5], "color": "rgba(0,255,156,0.12)"},
        {"range": [0.5, 1.5], "color": "rgba(255,215,0,0.12)"},
        {"range": [1.5, 3.0], "color": "rgba(255,140,0,0.12)"},
        {"range": [3.0, 5.0], "color": "rgba(255,51,51,0.12)"},
    ],
}

# ============================================================
# STYLING
# ============================================================

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600;700&family=Share+Tech+Mono&display=swap');

html, body, .stApp {
    background-color: #060C14;
    color: #E0E8F0;
    font-family: 'Rajdhani', sans-serif;
}
h1, h2, h3 {
    font-family: 'Rajdhani', sans-serif;
    letter-spacing: 2px;
}
.stApp:before {
    content: "";
    position: fixed;
    inset: 0;
    background:
        radial-gradient(ellipse at 20% 20%, rgba(0,80,160,0.15) 0%, transparent 60%),
        radial-gradient(ellipse at 80% 80%, rgba(0,40,80,0.2) 0%, transparent 60%);
    z-index: 0;
    pointer-events: none;
}
section.main > div {
    position: relative;
    z-index: 1;
}
.site-header {
    border-left: 6px solid #0088FF;
    padding: 12px 20px;
    margin-bottom: 24px;
    background: rgba(0,136,255,0.06);
    border-radius: 0 8px 8px 0;
}
.site-title {
    font-size: 2.8em;
    font-weight: 700;
    color: #FFFFFF;
    margin: 0;
    letter-spacing: 3px;
}
.site-sub {
    font-size: 1.1em;
    color: #7AACCC;
    text-transform: uppercase;
    font-family: 'Share Tech Mono', monospace;
}
.panel {
    background: rgba(10,20,35,0.85);
    border: 1px solid rgba(0,136,255,0.2);
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 16px;
}
.panel-title {
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.78em;
    color: #0088FF;
    text-transform: uppercase;
    letter-spacing: 3px;
    margin-bottom: 14px;
    border-bottom: 1px solid rgba(0,136,255,0.2);
    padding-bottom: 8px;
}
.source-badge {
    display: inline-block;
    background: rgba(0,136,255,0.12);
    border: 1px solid rgba(0,136,255,0.3);
    border-radius: 20px;
    padding: 2px 10px;
    font-size: 0.72em;
    color: #7AACCC;
    font-family: 'Share Tech Mono', monospace;
    margin: 2px;
}
.data-card {
    background: rgba(0,136,255,0.04);
    border: 1px solid rgba(0,136,255,0.14);
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 10px;
}
.data-card-title {
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.72em;
    color: #7AACCC;
    text-transform: uppercase;
    margin-bottom: 6px;
}
.data-card-value {
    font-size: 1.3em;
    font-weight: 700;
    color: #FFFFFF;
}
.small-muted {
    color: #7AACCC;
    font-size: 0.85em;
}
.forecast-row {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 10px;
}
.forecast-tile {
    background: rgba(0,136,255,0.05);
    border: 1px solid rgba(0,136,255,0.16);
    border-radius: 10px;
    padding: 12px;
    text-align: center;
}
.forecast-day {
    font-family: 'Share Tech Mono', monospace;
    color: #7AACCC;
    font-size: 0.75em;
    margin-bottom: 6px;
}
.forecast-hi {
    font-size: 1.2em;
    font-weight: 700;
    color: #FFFFFF;
}
.forecast-lo {
    font-size: 0.95em;
    color: #7AACCC;
}
hr.soft {
    border: none;
    border-top: 1px solid rgba(0,136,255,0.15);
    margin: 10px 0;
}
</style>
""",
    unsafe_allow_html=True,
)

# ============================================================
# HELPERS
# ============================================================

def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def safe_get(url: str, *, params=None, timeout=10):
    return requests.get(url, params=params, timeout=timeout)


def ok_payload(data=None, source=None, error=None):
    return {
        "ok": error is None,
        "data": data if data is not None else {},
        "source": source,
        "error": error,
    }


def first_non_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def clamp(value, low, high):
    return max(low, min(high, value))


def format_num(value, digits=1, suffix=""):
    if value is None:
        return "--"
    return f"{round(value, digits)}{suffix}"


# ============================================================
# SCIENCE / CALCS
# ============================================================

def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def calc_dewpoint_f(temp_f, humidity):
    if temp_f is None or humidity is None or humidity <= 0:
        return None
    temp_c = (temp_f - 32) * 5 / 9
    a, b = 17.625, 243.04
    try:
        alpha = math.log(humidity / 100.0) + (a * temp_c) / (b + temp_c)
        dp_c = (b * alpha) / (a - alpha)
        return round(dp_c * 9 / 5 + 32, 1)
    except Exception:
        return None


def calc_fog_spread(temp_f, dewpoint_f):
    if temp_f is None or dewpoint_f is None:
        return None
    return round(temp_f - dewpoint_f, 1)


def weather_desc(code):
    codes = {
        0: "Clear",
        1: "Mainly Clear",
        2: "Partly Cloudy",
        3: "Overcast",
        45: "Foggy",
        48: "Rime Fog",
        51: "Lt Drizzle",
        53: "Drizzle",
        55: "Heavy Drizzle",
        61: "Lt Rain",
        63: "Rain",
        65: "Heavy Rain",
        71: "Lt Snow",
        73: "Snow",
        75: "Heavy Snow",
        80: "Rain Showers",
        81: "Mod Showers",
        82: "Heavy Showers",
        95: "Thunderstorm",
        96: "Tstm+Hail",
        99: "Tstm+Heavy Hail",
    }
    return codes.get(code, "Unknown")


def pop_color(pop):
    if pop < 20:
        return COLORS["green"]
    if pop < 40:
        return COLORS["lime"]
    if pop < 60:
        return COLORS["yellow"]
    if pop < 80:
        return COLORS["orange"]
    return COLORS["red"]


def estimate_soil_moisture_belk(rain_30d, today_rain=0.0, profile="belk_compacted"):
    """
    Relative wetness model for Belk Building area.
    This is a dashboard index, not a direct in-ground soil sensor.
    """

    profiles = {
        "belk_compacted": {
            "field_capacity": 2.40,
            "wilting_point": 1.55,
            "max_storage": 3.10,
            "base_infiltration_eff": 0.55,
            "drainage_coeff": 0.030,
            "recent_rain_boost": 1.20,
        },
        "cullowhee_alluvial": {
            "field_capacity": 2.20,
            "wilting_point": 1.60,
            "max_storage": 2.90,
            "base_infiltration_eff": 0.72,
            "drainage_coeff": 0.045,
            "recent_rain_boost": 1.10,
        },
        "urban_fill_clayey": {
            "field_capacity": 2.60,
            "wilting_point": 1.70,
            "max_storage": 3.25,
            "base_infiltration_eff": 0.45,
            "drainage_coeff": 0.025,
            "recent_rain_boost": 1.25,
        },
    }

    p = profiles.get(profile, profiles["belk_compacted"])

    monthly_et = {
        1: 0.04, 2: 0.06, 3: 0.10, 4: 0.14, 5: 0.17, 6: 0.20,
        7: 0.21, 8: 0.19, 9: 0.14, 10: 0.09, 11: 0.05, 12: 0.03,
    }

    field_capacity = p["field_capacity"]
    wilting_point = p["wilting_point"]
    max_storage = p["max_storage"]
    infil_eff = p["base_infiltration_eff"]
    drainage_coeff = p["drainage_coeff"]
    recent_rain_boost = p["recent_rain_boost"]

    storage = field_capacity
    today = now_local().date()
    dates = [today - timedelta(days=(len(rain_30d) - 1 - i)) for i in range(len(rain_30d))]

    for i, (rain, d) in enumerate(zip(rain_30d, dates)):
        rain = rain or 0.0
        et_daily = monthly_et.get(d.month, 0.10)

        days_ago = len(rain_30d) - 1 - i
        weight = recent_rain_boost if days_ago <= 7 else 1.0

        if rain <= 0.30:
            effective_rain = rain * infil_eff
        elif rain <= 1.00:
            effective_rain = (0.30 * infil_eff) + ((rain - 0.30) * infil_eff * 0.80)
        else:
            effective_rain = (
                (0.30 * infil_eff)
                + (0.70 * infil_eff * 0.80)
                + ((rain - 1.00) * infil_eff * 0.45)
            )

        effective_rain *= weight

        drainage = max(0.0, (storage - field_capacity) * drainage_coeff)

        storage = storage + effective_rain - et_daily - drainage
        storage = max(wilting_point, min(max_storage, storage))

    storage = min(max_storage, storage + (today_rain or 0.0) * infil_eff)

    pct = ((storage - wilting_point) / (max_storage - wilting_point)) * 100
    pct = clamp(pct, 0, 100)

    if pct >= 90:
        return round(pct, 1), "SATURATED", COLORS["red"], round(storage, 2)
    elif pct >= 75:
        return round(pct, 1), "WET", COLORS["orange"], round(storage, 2)
    elif pct >= 50:
        return round(pct, 1), "MOIST", COLORS["yellow"], round(storage, 2)
    elif pct >= 25:
        return round(pct, 1), "ADEQUATE", COLORS["green"], round(storage, 2)
    else:
        return round(pct, 1), "DRY", COLORS["blue"], round(storage, 2)


# ============================================================
# DATA FETCHERS
# ============================================================

@st.cache_data(ttl=300)
def fetch_ambient():
    try:
        r = safe_get(
            "https://api.ambientweather.net/v1/devices",
            params={"apiKey": AMBIENT_API_KEY, "applicationKey": AMBIENT_APP_KEY},
            timeout=10,
        )
        r.raise_for_status()
        devices = r.json() or []

        if not devices:
            return ok_payload(source="AMBIENT", error="No devices returned")

        target = next(
            (
                d for d in devices
                if d.get("macAddress", "").replace(":", "").replace("-", "").lower() == AMBIENT_DEVICE_MAC
            ),
            devices[0],
        )

        last = target.get("lastData", {})
        data = {
            "temp": last.get("tempf"),
            "humidity": last.get("humidity"),
            "wind_speed": last.get("windspeedmph", 0),
            "wind_dir": last.get("winddir", 0),
            "wind_gust": last.get("windgustmph", 0),
            "rain_today": last.get("dailyrainin", 0.0),
            "rain_1hr": last.get("hourlyrainin", 0.0),
            "rain_week": last.get("weeklyrainin", 0.0),
            "rain_month": last.get("monthlyrainin", 0.0),
            "pressure": last.get("baromrelin"),
            "uv": last.get("uv", 0),
            "solar": last.get("solarradiation", 0),
            "lightning_dist": last.get("lightning_distance"),
            "lightning_day": last.get("lightning_day", 0),
            "lightning_hour": last.get("lightning_hour", 0),
            "name": target.get("info", {}).get("name", "Ambient Station"),
        }
        return ok_payload(data=data, source="AMBIENT")
    except Exception as e:
        return ok_payload(source="AMBIENT", error=str(e))


@st.cache_data(ttl=60)
def fetch_blitzortung_lightning():
    try:
        utc_now = datetime.utcnow()
        closest_dist = None

        for minutes_back in range(0, 31):
            t = utc_now - timedelta(minutes=minutes_back)
            url = (
                "https://data.blitzortung.org/Data/Protected/By_Location/"
                "By_Region/America/Strokes/"
                f"{t.year}/{t.month:02d}/{t.day:02d}/{t.hour:02d}/{t.minute:02d}.json"
            )
            try:
                r = safe_get(url, timeout=4)
                if r.status_code != 200 or not r.text.strip():
                    continue

                strokes = r.json()
                for stroke in strokes:
                    slat = stroke.get("lat") or stroke.get("y")
                    slon = stroke.get("lon") or stroke.get("x")
                    if slat is None or slon is None:
                        continue
                    dist = haversine_miles(LAT, LON, float(slat), float(slon))
                    if closest_dist is None or dist < closest_dist:
                        closest_dist = dist
            except Exception:
                continue

        if closest_dist is None:
            return ok_payload(source="BLITZORTUNG", error="No strokes found")

        return ok_payload(data={"dist": round(closest_dist, 1)}, source="BLITZORTUNG")
    except Exception as e:
        return ok_payload(source="BLITZORTUNG", error=str(e))


def resolve_lightning(ambient_payload, blitz_payload):
    ambient = ambient_payload.get("data", {})
    blitz = blitz_payload.get("data", {})

    awn_dist = ambient.get("lightning_dist")
    blitz_dist = blitz.get("dist")

    detected_distances = []

    if awn_dist is not None:
        try:
            detected_distances.append(float(awn_dist))
        except Exception:
            pass

    if blitz_dist is not None:
        try:
            detected_distances.append(float(blitz_dist))
        except Exception:
            pass

    if detected_distances:
        final_dist = min(detected_distances)
        if awn_dist is not None and blitz_dist is not None:
            source_tag = "AWN + BLITZ"
        elif awn_dist is not None:
            source_tag = "AWN ONLY"
        else:
            source_tag = "BLITZ ONLY"
        strike_detected = True
    else:
        final_dist = 0.0
        source_tag = "NO STRIKES"
        strike_detected = False

    strikes = ambient.get("lightning_day", 0) or 0
    return round(final_dist, 1), source_tag, strikes, strike_detected


@st.cache_data(ttl=1800)
def fetch_aqi():
    try:
        r = safe_get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={
                "latitude": LAT,
                "longitude": LON,
                "hourly": "us_aqi",
                "timezone": "America/New_York",
                "forecast_days": 1,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        aqi_vals = data.get("hourly", {}).get("us_aqi", [])
        current_hour = now_local().hour

        candidate_values = []
        if current_hour < len(aqi_vals):
            candidate_values.append(aqi_vals[current_hour])
        candidate_values.extend(aqi_vals)

        aqi = next((v for v in candidate_values if v is not None), 0)
        return ok_payload(data={"aqi": int(aqi)}, source="OPEN-METEO AQI")
    except Exception as e:
        return ok_payload(source="OPEN-METEO AQI", error=str(e))


@st.cache_data(ttl=300)
def fetch_airport_metar():
    try:
        r = safe_get(
            "https://aviationweather.gov/api/data/metar",
            params={"ids": AIRPORT_ID, "format": "json"},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json() or []

        if not data:
            return ok_payload(source="AVIATION WEATHER", error="No METAR returned")

        obs = data[0]
        payload = {
            "temp_f": round(obs.get("temp", 0) * 9 / 5 + 32, 1) if obs.get("temp") is not None else None,
            "wind_mph": round(obs.get("wspd", 0) * 1.15078, 1) if obs.get("wspd") else None,
            "wind_dir": obs.get("wdir"),
            "altim": obs.get("altim"),
            "precip": obs.get("precip", 0.0),
            "cover": obs.get("skyCondition", [{}])[0].get("skyCover", "CLR") if obs.get("skyCondition") else "CLR",
            "raw": obs.get("rawOb", ""),
            "time": obs.get("obsTime", ""),
        }
        return ok_payload(data=payload, source="AVIATION WEATHER")
    except Exception as e:
        return ok_payload(source="AVIATION WEATHER", error=str(e))


@st.cache_data(ttl=300)
def fetch_usgs_rain():
    results = {}
    for site_id, name in USGS_GAUGES.items():
        try:
            r = safe_get(
                "https://waterservices.usgs.gov/nwis/iv/",
                params={"format": "json", "sites": site_id, "parameterCd": "00045"},
                timeout=8,
            )
            r.raise_for_status()
            data = r.json()
            val = float(data["value"]["timeSeries"][0]["values"][0]["value"][0]["value"])
            results[site_id] = {"name": name, "value": val, "ok": True, "error": None}
        except Exception as e:
            results[site_id] = {"name": name, "value": 0.0, "ok": False, "error": str(e)}
    return results


@st.cache_data(ttl=600)
def fetch_multimodel_forecast():
    base_params = {
        "latitude": LAT,
        "longitude": LON,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weathercode,windspeed_10m_max",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "windspeed_unit": "mph",
        "timezone": "America/New_York",
        "forecast_days": 7,
    }

    forecasts = {}
    errors = {}

    for model_key, model_str in MODEL_PARAMS.items():
        try:
            params = {**base_params, "models": model_str}
            r = safe_get("https://api.open-meteo.com/v1/forecast", params=params, timeout=12)
            r.raise_for_status()
            forecasts[model_key] = r.json().get("daily")
        except Exception as e:
            forecasts[model_key] = None
            errors[model_key] = str(e)

    days = []
    today = now_local()

    for i in range(7):
        date = today + timedelta(days=i)
        primary = "hrrr" if i <= 1 else "gfs"
        secondary = "gfs" if i <= 1 else "hrrr"
        src = forecasts.get(primary) or forecasts.get(secondary)
        model_label = primary.upper() if forecasts.get(primary) else secondary.upper()

        if src and i < len(src.get("time", [])):
            days.append(
                {
                    "date": date.strftime("%a %m/%d"),
                    "day": date.strftime("%a"),
                    "hi": round(src["temperature_2m_max"][i] or 0),
                    "lo": round(src["temperature_2m_min"][i] or 0),
                    "precip": round(src["precipitation_sum"][i] or 0, 2),
                    "pop": src["precipitation_probability_max"][i] or 0,
                    "wind": round(src["windspeed_10m_max"][i] or 0),
                    "code": src["weathercode"][i] or 0,
                    "model": model_label,
                    "desc": weather_desc(src["weathercode"][i] or 0),
                }
            )
        else:
            days.append(
                {
                    "date": date.strftime("%a %m/%d"),
                    "day": date.strftime("%a"),
                    "hi": 60,
                    "lo": 40,
                    "precip": 0.0,
                    "pop": 10,
                    "wind": 10,
                    "code": 0,
                    "model": "N/A",
                    "desc": "Unknown",
                }
            )

    return ok_payload(data={"days": days, "errors": errors}, source="OPEN-METEO")


@st.cache_data(ttl=3600)
def fetch_historical_rain_30d():
    try:
        end = now_local().date()
        start = end - timedelta(days=30)

        r = safe_get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": LAT,
                "longitude": LON,
                "daily": "precipitation_sum",
                "precipitation_unit": "inch",
                "timezone": "America/New_York",
                "start_date": start.strftime("%Y-%m-%d"),
                "end_date": end.strftime("%Y-%m-%d"),
            },
            timeout=12,
        )
        r.raise_for_status()
        vals = r.json().get("daily", {}).get("precipitation_sum", [])
        return ok_payload(data={"rain_30d": [v or 0.0 for v in vals]}, source="OPEN-METEO HIST")
    except Exception as e:
        return ok_payload(data={"rain_30d": [0.05] * 30}, source="OPEN-METEO HIST", error=str(e))


# ============================================================
# UI HELPERS
# ============================================================

def make_gauge(value, title, min_val=0, max_val=100, unit="%", thresholds=None, color=None):
    thresholds = thresholds or [
        {"range": [0, 25], "color": "rgba(0,255,156,0.15)"},
        {"range": [25, 50], "color": "rgba(255,215,0,0.15)"},
        {"range": [50, 75], "color": "rgba(255,140,0,0.15)"},
        {"range": [75, 100], "color": "rgba(255,51,51,0.15)"},
    ]

    if color is None:
        if value < 30:
            color = COLORS["green"]
        elif value < 55:
            color = COLORS["yellow"]
        elif value < 75:
            color = COLORS["orange"]
        else:
            color = COLORS["red"]

    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=value,
            number={
                "suffix": unit,
                "font": {"size": 26, "color": COLORS["white"], "family": "Rajdhani"},
            },
            title={
                "text": title,
                "font": {"size": 11, "color": COLORS["muted"], "family": "Share Tech Mono"},
            },
            gauge={
                "axis": {
                    "range": [min_val, max_val],
                    "tickwidth": 1,
                    "tickcolor": "#2A4060",
                    "tickfont": {"color": "#5A7A9A", "size": 8},
                },
                "bar": {"color": color, "thickness": 0.25},
                "bgcolor": "rgba(0,0,0,0)",
                "borderwidth": 0,
                "steps": thresholds,
                "threshold": {
                    "line": {"color": color, "width": 3},
                    "thickness": 0.85,
                    "value": value,
                },
            },
        )
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=35, b=5, l=15, r=15),
        height=185,
        font={"color": "#E0E8F0"},
    )
    return fig


def sublabel(text, color=COLORS["muted"]):
    return (
        f"<div style='text-align:center;font-family:Rajdhani;font-size:1.2em;"
        f"font-weight:700;color:{color};margin-top:2px;'>{text}</div>"
    )


def subsub(text, color=COLORS["muted"]):
    return (
        f"<div style='text-align:center;font-family:Rajdhani;font-size:0.85em;"
        f"color:{color};'>{text}</div>"
    )


def srctag(text):
    return (
        "<div style='text-align:center;font-family:Share Tech Mono,monospace;"
        f"font-size:0.62em;color:#2A6080;margin-top:1px;'>SRC: {text}</div>"
    )


def render_gauge_card(column, config):
    with column:
        fig = make_gauge(
            config["value"],
            config["title"],
            min_val=config.get("min_val", 0),
            max_val=config.get("max_val", 100),
            unit=config.get("unit", "%"),
            thresholds=config.get("thresholds"),
            color=config.get("color"),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        st.markdown(sublabel(config["label"], config["color"]), unsafe_allow_html=True)
        st.markdown(subsub(config["detail"]), unsafe_allow_html=True)
        st.markdown(srctag(config["source"]), unsafe_allow_html=True)


def render_source_status_badge(label, is_live):
    return f"<span class='source-badge'>{label}: {'LIVE' if is_live else 'OFFLINE'}</span>"


def render_data_card(title, value, detail=None):
    st.markdown(
        f"""
        <div class="data-card">
            <div class="data-card-title">{title}</div>
            <div class="data-card-value">{value}</div>
            <div class="small-muted">{detail or ''}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# LOAD DATA
# ============================================================

with st.spinner("Syncing all data sources..."):
    ambient_payload = fetch_ambient()
    blitz_payload = fetch_blitzortung_lightning()
    aqi_payload = fetch_aqi()
    airport_payload = fetch_airport_metar()
    usgs_data = fetch_usgs_rain()
    forecast_payload = fetch_multimodel_forecast()
    hist_payload = fetch_historical_rain_30d()

ambient = ambient_payload.get("data", {})
aqi_data = aqi_payload.get("data", {})
airport = airport_payload.get("data", {})
forecast = forecast_payload.get("data", {}).get("days", [])
hist_rain = hist_payload.get("data", {}).get("rain_30d", [0.05] * 30)

# ============================================================
# DERIVED METRICS
# ============================================================

rain_today = 0.0
if ambient_payload["ok"]:
    rain_today = ambient.get("rain_today", 0.0) or 0.0
elif airport_payload["ok"]:
    rain_today = airport.get("precip", 0.0) or 0.0

rain_3d_forecast = sum(day["precip"] for day in forecast[:3]) if forecast else 0.0
wind_now = first_non_none(ambient.get("wind_speed"), airport.get("wind_mph"), 0) or 0
pop_today = forecast[0]["pop"] if forecast else 0

soil_pct, soil_status, soil_color, soil_storage = estimate_soil_moisture_belk(
    hist_rain,
    rain_today,
    profile="belk_compacted"
)

l_dist, l_source, l_strikes, lightning_detected = resolve_lightning(ambient_payload, blitz_payload)
if lightning_detected:
    l_display = min(l_dist, 25)
    l_color = (
        COLORS["red"] if l_dist < 5 else
        COLORS["orange"] if l_dist < 10 else
        COLORS["yellow"] if l_dist < 15 else
        COLORS["green"]
    )
    l_label = (
        "CRITICAL" if l_dist < 5 else
        "NEARBY" if l_dist < 10 else
        "MODERATE" if l_dist < 15 else
        "DISTANT"
    )
    l_detail = f"Nearest Strike: <b style='color:#00FFCC'>{l_dist:.1f} mi</b>"
else:
    l_display = 0.0
    l_color = COLORS["green"]
    l_label = "NO STRIKES"
    l_detail = "No lightning detected nearby"

uv_val = ambient.get("uv", 0) if ambient_payload["ok"] else 0
uv_val = uv_val or 0
uv_color = (
    COLORS["green"] if uv_val <= 2 else
    COLORS["lime"] if uv_val <= 5 else
    COLORS["yellow"] if uv_val <= 7 else
    COLORS["orange"] if uv_val <= 10 else
    COLORS["red"]
)
uv_label = (
    "LOW" if uv_val <= 2 else
    "MODERATE" if uv_val <= 5 else
    "HIGH" if uv_val <= 7 else
    "VERY HIGH" if uv_val <= 10 else
    "EXTREME"
)

temp_now = first_non_none(ambient.get("temp"), airport.get("temp_f"))
temp_display = clamp(temp_now if temp_now is not None else 70, 0, 120)
temp_color = (
    COLORS["blue"] if temp_display < 32 else
    COLORS["cyan"] if temp_display < 50 else
    COLORS["green"] if temp_display < 80 else
    COLORS["yellow"] if temp_display < 95 else
    COLORS["orange"] if temp_display < 105 else
    COLORS["red"]
)
temp_label = (
    "FREEZING" if temp_display < 32 else
    "COLD" if temp_display < 50 else
    "MILD" if temp_display < 80 else
    "WARM" if temp_display < 95 else
    "HOT" if temp_display < 105 else
    "EXTREME"
)

hum_now = ambient.get("humidity") if ambient_payload["ok"] else None
hum_val = hum_now or 0
hum_color = (
    COLORS["blue"] if hum_val < 30 else
    COLORS["green"] if hum_val < 60 else
    COLORS["yellow"] if hum_val < 80 else
    COLORS["orange"]
)
hum_label = (
    "DRY" if hum_val < 30 else
    "COMFORTABLE" if hum_val < 60 else
    "HUMID" if hum_val < 80 else
    "VERY HUMID"
)

tonight_low = forecast[0]["lo"] if forecast else 50
freeze_display = clamp(tonight_low, 0, 80)
freeze_color = (
    COLORS["green"] if tonight_low > 45 else
    COLORS["yellow"] if tonight_low > 32 else
    COLORS["orange"] if tonight_low > 28 else
    COLORS["red"]
)
freeze_label = (
    "NO RISK" if tonight_low > 45 else
    "WATCH" if tonight_low > 32 else
    "FREEZE" if tonight_low > 28 else
    "HARD FREEZE"
)

dp_val = calc_dewpoint_f(temp_now, hum_now)
fog_spread = calc_fog_spread(temp_now, dp_val)
fog_display = clamp(fog_spread if fog_spread is not None else 20, 0, 30)
fog_color = (
    COLORS["red"] if fog_display < 3 else
    COLORS["orange"] if fog_display < 9 else
    COLORS["yellow"] if fog_display < 18 else
    COLORS["green"]
)
fog_label = (
    "FOG IMMINENT" if fog_display < 3 else
    "HIGH RISK" if fog_display < 9 else
    "MODERATE" if fog_display < 18 else
    "CLEAR"
)

aqi_val = aqi_data.get("aqi", 0) if aqi_payload["ok"] else 0
aqi_display = min(aqi_val, 200)
aqi_color = (
    COLORS["green"] if aqi_val <= 50 else
    COLORS["lime"] if aqi_val <= 100 else
    COLORS["yellow"] if aqi_val <= 150 else
    COLORS["orange"] if aqi_val <= 200 else
    COLORS["red"]
)
aqi_label = (
    "GOOD" if aqi_val <= 50 else
    "MODERATE" if aqi_val <= 100 else
    "SENSITIVE" if aqi_val <= 150 else
    "UNHEALTHY" if aqi_val <= 200 else
    "HAZARDOUS"
)

rain3d_display = clamp(rain_3d_forecast, 0, 5)
rain3d_color = (
    COLORS["green"] if rain_3d_forecast < 0.5 else
    COLORS["yellow"] if rain_3d_forecast < 1.5 else
    COLORS["orange"] if rain_3d_forecast < 3.0 else
    COLORS["red"]
)
rain3d_label = (
    "LIGHT" if rain_3d_forecast < 0.5 else
    "ELEVATED" if rain_3d_forecast < 1.5 else
    "HEAVY" if rain_3d_forecast < 3.0 else
    "SIGNIFICANT"
)

pressure_now = first_non_none(ambient.get("pressure"), 29.92)
current_time = now_local()
tz_label = current_time.tzname() or "ET"

# ============================================================
# HEADER
# ============================================================

st.markdown(
    f"""
<div class="site-header">
    <div class="site-title">WCU BELK WEATHER INTELLIGENCE</div>
    <div class="site-sub">
        {SITE} &nbsp;|&nbsp; {current_time.strftime('%A, %B %d, %Y %I:%M %p')} {tz_label}
    </div>
    <div style="margin-top:8px;">
        {render_source_status_badge("📡 AWN", ambient_payload["ok"])}
        {render_source_status_badge("⚡ BLITZORTUNG", blitz_payload["ok"])}
        {render_source_status_badge("✈️ AIRPORT 24A", airport_payload["ok"])}
        {render_source_status_badge("💧 USGS", any(v["ok"] for v in usgs_data.values()))}
        {render_source_status_badge("🌬️ AQI", aqi_payload["ok"])}
        {render_source_status_badge("🌐 OPEN-METEO", True)}
    </div>
</div>
""",
    unsafe_allow_html=True,
)

# ============================================================
# GAUGE ROW 1
# ============================================================

st.markdown('<div class="panel"><div class="panel-title">⚡ Hazard & Atmospheric Gauges</div>', unsafe_allow_html=True)
cols = st.columns(5)

row1 = [
    {
        "title": "LIGHTNING PROXIMITY",
        "value": l_display,
        "min_val": 0,
        "max_val": 25,
        "unit": " mi",
        "thresholds": GAUGE_THRESHOLDS["lightning"],
        "color": l_color,
        "label": l_label,
        "detail": l_detail,
        "source": l_source,
    },
    {
        "title": "UV INDEX",
        "value": uv_val,
        "min_val": 0,
        "max_val": 12,
        "unit": "",
        "thresholds": GAUGE_THRESHOLDS["uv"],
        "color": uv_color,
        "label": uv_label,
        "detail": "Protect skin >3 | Seek shade >6",
        "source": "AWN SENSOR",
    },
    {
        "title": "AIR TEMPERATURE",
        "value": temp_display,
        "min_val": 0,
        "max_val": 120,
        "unit": "°F",
        "thresholds": GAUGE_THRESHOLDS["temp"],
        "color": temp_color,
        "label": temp_label,
        "detail": f"Dewpoint: <b style='color:#00FFCC'>{format_num(dp_val, 1, '°F')}</b>",
        "source": "AWN SENSOR" if ambient_payload["ok"] else "AIRPORT METAR",
    },
    {
        "title": "HUMIDITY",
        "value": hum_val,
        "min_val": 0,
        "max_val": 100,
        "unit": "%",
        "thresholds": GAUGE_THRESHOLDS["humidity"],
        "color": hum_color,
        "label": hum_label,
        "detail": f"Dewpoint: <b style='color:#00FFCC'>{format_num(dp_val, 1, '°F')}</b>",
        "source": "AWN SENSOR",
    },
    {
        "title": "WIND SPEED",
        "value": wind_now,
        "min_val": 0,
        "max_val": 60,
        "unit": " mph",
        "thresholds": GAUGE_THRESHOLDS["wind"],
        "color": (
            COLORS["green"] if wind_now < 15 else
            COLORS["yellow"] if wind_now < 25 else
            COLORS["orange"] if wind_now < 35 else
            COLORS["red"]
        ),
        "label": (
            "CALM" if wind_now < 15 else
            "BREEZY" if wind_now < 25 else
            "STRONG" if wind_now < 35 else
            "DANGEROUS"
        ),
        "detail": f"Gust: <b style='color:#00FFCC'>{format_num(ambient.get('wind_gust'), 1, ' mph')}</b>",
        "source": "AWN SENSOR" if ambient_payload["ok"] else "AIRPORT METAR",
    },
]

for col, config in zip(cols, row1):
    render_gauge_card(col, config)

st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# GAUGE ROW 2
# ============================================================

st.markdown('<div class="panel"><div class="panel-title">🌱 Site Condition Gauges</div>', unsafe_allow_html=True)
cols = st.columns(5)

row2 = [
    {
        "title": "RELATIVE SOIL WETNESS",
        "value": soil_pct,
        "min_val": 0,
        "max_val": 100,
        "unit": "%",
        "thresholds": GAUGE_THRESHOLDS["soil"],
        "color": soil_color,
        "label": soil_status,
        "detail": f"Belk profile storage: <b style='color:#00FFCC'>{soil_storage} in</b>",
        "source": "BELK COMPACTED MODEL",
    },
    {
        "title": "PRECIP PROBABILITY",
        "value": pop_today,
        "min_val": 0,
        "max_val": 100,
        "unit": "%",
        "thresholds": GAUGE_THRESHOLDS["precip_prob"],
        "color": pop_color(pop_today),
        "label": (
            "DRY" if pop_today < 20 else
            "SLIGHT" if pop_today < 40 else
            "CHANCE" if pop_today < 60 else
            "LIKELY" if pop_today < 80 else
            "CERTAIN"
        ),
        "detail": f"Today model: <b style='color:#00FFCC'>{forecast[0]['model'] if forecast else 'N/A'}</b>",
        "source": "OPEN-METEO",
    },
    {
        "title": "FREEZE RISK",
        "value": freeze_display,
        "min_val": 0,
        "max_val": 80,
        "unit": "°F",
        "thresholds": GAUGE_THRESHOLDS["freeze"],
        "color": freeze_color,
        "label": freeze_label,
        "detail": "Based on forecast low",
        "source": "OPEN-METEO",
    },
    {
        "title": "FOG SPREAD",
        "value": fog_display,
        "min_val": 0,
        "max_val": 30,
        "unit": "°F",
        "thresholds": GAUGE_THRESHOLDS["fog"],
        "color": fog_color,
        "label": fog_label,
        "detail": f"T - Td spread: <b style='color:#00FFCC'>{format_num(fog_spread, 1, '°F')}</b>",
        "source": "AWN + CALC",
    },
    {
        "title": "AIR QUALITY",
        "value": aqi_display,
        "min_val": 0,
        "max_val": 200,
        "unit": "",
        "thresholds": GAUGE_THRESHOLDS["aqi"],
        "color": aqi_color,
        "label": aqi_label,
        "detail": "US AQI scale",
        "source": "OPEN-METEO AQI",
    },
]

for col, config in zip(cols, row2):
    render_gauge_card(col, config)

st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# GAUGE ROW 3
# ============================================================

st.markdown('<div class="panel"><div class="panel-title">🌧️ Rain & Short-Term Impact</div>', unsafe_allow_html=True)
cols = st.columns(3)

row3 = [
    {
        "title": "TODAY RAIN",
        "value": clamp(rain_today, 0, 5),
        "min_val": 0,
        "max_val": 5,
        "unit": " in",
        "thresholds": GAUGE_THRESHOLDS["rain3d"],
        "color": (
            COLORS["green"] if rain_today < 0.25 else
            COLORS["yellow"] if rain_today < 1.0 else
            COLORS["orange"] if rain_today < 2.0 else
            COLORS["red"]
        ),
        "label": (
            "LIGHT" if rain_today < 0.25 else
            "MODERATE" if rain_today < 1.0 else
            "HEAVY" if rain_today < 2.0 else
            "SIGNIFICANT"
        ),
        "detail": f"1 hr rain: <b style='color:#00FFCC'>{format_num(ambient.get('rain_1hr'), 2, ' in')}</b>",
        "source": "AWN SENSOR" if ambient_payload["ok"] else "AIRPORT METAR",
    },
    {
        "title": "3-DAY RAIN",
        "value": rain3d_display,
        "min_val": 0,
        "max_val": 5,
        "unit": " in",
        "thresholds": GAUGE_THRESHOLDS["rain3d"],
        "color": rain3d_color,
        "label": rain3d_label,
        "detail": "Sum of next 3 forecast days",
        "source": "OPEN-METEO",
    },
    {
        "title": "PRESSURE",
        "value": clamp(pressure_now or 29.92, 28, 32),
        "min_val": 28,
        "max_val": 32,
        "unit": " inHg",
        "thresholds": [
            {"range": [28, 29], "color": "rgba(255,51,51,0.12)"},
            {"range": [29, 29.8], "color": "rgba(255,140,0,0.12)"},
            {"range": [29.8, 30.3], "color": "rgba(0,255,156,0.12)"},
            {"range": [30.3, 32], "color": "rgba(90,200,250,0.12)"},
        ],
        "color": COLORS["cyan"],
        "label": "BAROMETRIC",
        "detail": f"Solar: <b style='color:#00FFCC'>{format_num(ambient.get('solar'), 0, ' W/m²')}</b>",
        "source": "AWN SENSOR",
    },
]

for col, config in zip(cols, row3):
    render_gauge_card(col, config)

st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# FORECAST
# ============================================================

st.markdown('<div class="panel"><div class="panel-title">📅 Seven-Day Forecast</div>', unsafe_allow_html=True)

forecast_html = ['<div class="forecast-row">']
for day in forecast:
    forecast_html.append(
        f"""
        <div class="forecast-tile">
            <div class="forecast-day">{day['date']}</div>
            <div class="forecast-hi">{day['hi']}°</div>
            <div class="forecast-lo">Low {day['lo']}°</div>
            <div class="small-muted">{day['desc']}</div>
            <hr class="soft">
            <div class="small-muted">Rain: {day['precip']} in</div>
            <div class="small-muted">PoP: {day['pop']}%</div>
            <div class="small-muted">Wind: {day['wind']} mph</div>
            <div class="small-muted">Model: {day['model']}</div>
        </div>
        """
    )
forecast_html.append("</div>")
st.markdown("".join(forecast_html), unsafe_allow_html=True)
st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# DETAIL PANELS
# ============================================================

left, right = st.columns([1.2, 1.0])

with left:
    st.markdown('<div class="panel"><div class="panel-title">📡 Sensor & Observation Snapshot</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)

    with c1:
        render_data_card("Air Temperature", format_num(temp_now, 1, "°F"), "Ambient / airport fallback")
        render_data_card("Humidity", format_num(hum_now, 0, "%"), "Ambient station")
        render_data_card("Wind Gust", format_num(ambient.get("wind_gust"), 1, " mph"), "Ambient station")
        render_data_card("Rain Today", format_num(rain_today, 2, " in"), "Ambient / airport fallback")

    with c2:
        render_data_card("Dew Point", format_num(dp_val, 1, "°F"), "Calculated")
        render_data_card("Fog Spread", format_num(fog_spread, 1, "°F"), "Calculated")
        render_data_card("Lightning Distance", format_num(l_dist, 1, " mi"), l_source)
        render_data_card("AQI", format_num(aqi_val, 0, ""), "Open-Meteo AQI")

    st.markdown("</div>", unsafe_allow_html=True)

with right:
    st.markdown('<div class="panel"><div class="panel-title">💧 USGS / Aviation / Diagnostics</div>', unsafe_allow_html=True)

    for site_id, item in usgs_data.items():
        status = "LIVE" if item["ok"] else "OFFLINE"
        render_data_card(
            f"USGS {item['name']}",
            format_num(item["value"], 2, " in"),
            f"Gauge {site_id} • {status}",
        )

    render_data_card("Airport METAR", AIRPORT_ID, airport.get("raw", "No raw observation"))
    render_data_card("Forecast Source", "Open-Meteo", "HRRR near term, GFS farther out")
    render_data_card("Soil Wetness Profile", "belk_compacted", "Compacted campus-ground assumption")

    if ambient_payload["error"] or blitz_payload["error"] or aqi_payload["error"] or airport_payload["error"] or hist_payload["error"]:
        st.markdown("<hr class='soft'>", unsafe_allow_html=True)
        st.markdown("<div class='data-card-title'>Diagnostics</div>", unsafe_allow_html=True)
        for payload in [ambient_payload, blitz_payload, aqi_payload, airport_payload, hist_payload]:
            if payload.get("error"):
                st.markdown(
                    f"<div class='small-muted'><b>{payload.get('source')}:</b> {payload.get('error')}</div>",
                    unsafe_allow_html=True,
                )

    st.markdown("</div>", unsafe_allow_html=True)
