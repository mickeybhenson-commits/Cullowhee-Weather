import json
import math
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import folium
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from streamlit_folium import st_folium

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

# ── FIX: AWN requires two separate keys ──
AMBIENT_API_KEY = st.secrets.get(
    "AMBIENT_API_KEY",
    "4112bd1931ce4b7a9ba75da04c237db348c6f56e18dc4616966bd3e6474fac04",
)
AMBIENT_APP_KEY = st.secrets.get(
    "AMBIENT_APP_KEY",
    "8a162b733aed482a94a00abdacfe16165dd8f24c131b464290685fa77e872629",
)

AMBIENT_STATION_NAME = "riverbend"
AIRPORT_ID = "K24A"
NWS_USER_AGENT = "(WCU-Belk-Weather/1.0 mickey.b.henson@gmail.com)"

USGS_GAUGES = {
    "03439000": "Tuckasegee @ Cullowhee",
    "03460000": "Tuckasegee @ Bryson City",
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
    "moon": [
        {"range": [0, 12.5], "color": "rgba(90,200,250,0.12)"},
        {"range": [12.5, 37.5], "color": "rgba(0,255,156,0.12)"},
        {"range": [37.5, 62.5], "color": "rgba(255,215,0,0.12)"},
        {"range": [62.5, 87.5], "color": "rgba(255,140,0,0.12)"},
        {"range": [87.5, 100], "color": "rgba(255,255,255,0.16)"},
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
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 10px;
    width: 100%;
}
.forecast-tile {
    background: rgba(0,136,255,0.05);
    border: 1px solid rgba(0,136,255,0.16);
    border-radius: 10px;
    padding: 12px;
    text-align: center;
    min-width: 0;
}
.forecast-tile-today {
    background: rgba(0,136,255,0.13);
    border: 1px solid rgba(0,210,255,0.50);
    border-radius: 10px;
    padding: 12px;
    text-align: center;
    min-width: 0;
    box-shadow: 0 0 16px rgba(0,180,255,0.18);
}
.forecast-day {
    font-family: 'Share Tech Mono', monospace;
    color: #7AACCC;
    font-size: 0.75em;
    margin-bottom: 4px;
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


def safe_get(url: str, *, params=None, timeout=10, headers=None):
    return requests.get(url, params=params, timeout=timeout, headers=headers)


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


def weather_desc(code):
    codes = {
        0: "Clear", 1: "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast",
        45: "Foggy", 48: "Rime Fog", 51: "Lt Drizzle", 53: "Drizzle",
        55: "Heavy Drizzle", 61: "Lt Rain", 63: "Rain", 65: "Heavy Rain",
        71: "Lt Snow", 73: "Snow", 75: "Heavy Snow", 80: "Rain Showers",
        81: "Mod Showers", 82: "Heavy Showers", 95: "Thunderstorm",
        96: "Tstm+Hail", 99: "Tstm+Heavy Hail",
    }
    return codes.get(code, "Unknown")


def nws_desc_to_code(desc):
    d = desc.lower()
    if any(x in d for x in ["thunderstorm", "tstm", "lightning"]):
        return 95
    if any(x in d for x in ["blizzard", "heavy snow", "snow"]):
        return 73
    if any(x in d for x in ["sleet", "freezing rain", "wintry mix"]):
        return 73
    if any(x in d for x in ["shower", "showers"]):
        return 80
    if "rain" in d:
        return 63
    if "drizzle" in d:
        return 51
    if any(x in d for x in ["fog", "mist"]):
        return 45
    if any(x in d for x in ["overcast", "cloudy"]):
        return 3
    if any(x in d for x in ["mostly cloudy", "partly cloudy", "partly sunny"]):
        return 2
    if any(x in d for x in ["sunny", "clear", "fair", "mostly sunny", "mostly clear"]):
        return 0
    return 1


def weather_emoji(code):
    if code in [95, 96, 99]:
        return "⛈️"
    if code in [80, 81, 82]:
        return "🌦️"
    if code in [71, 73, 75]:
        return "❄️"
    if code in [61, 63, 65]:
        return "🌧️"
    if code in [51, 53, 55]:
        return "🌦️"
    if code in [45, 48]:
        return "🌫️"
    if code == 3:
        return "☁️"
    if code == 2:
        return "⛅"
    if code in [0, 1]:
        return "☀️"
    return "🌤️"


def pop_bar_color(pop):
    if pop < 20:
        return "#00FF9C"
    if pop < 40:
        return "#AAFF00"
    if pop < 60:
        return "#FFD700"
    if pop < 80:
        return "#FF8C00"
    return "#FF3333"


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


def build_forecast_tile(day, is_today=False):
    tile_class = "forecast-tile-today" if is_today else "forecast-tile"
    emoji = weather_emoji(day.get("code", 0))
    hi = f"{day['hi']}°" if day.get("hi") is not None else "--"
    lo = f"{day['lo']}°" if day.get("lo") is not None else "--"
    precip_val = day.get("precip")
    precip_str = f"{precip_val} in" if precip_val is not None else "--"
    pop = day.get("pop", 0)
    pbc = pop_bar_color(pop)
    src = day.get("source", "--")

    src_color_map = {
        "HRRR": "#00CCAA",
        "NWS":  "#5AC8FA",
        "ECMWF": "#AA88FF",
        "N/A":  "#2A6080",
    }
    src_color = src_color_map.get(src, "#2A6080")

    today_badge = (
        '<div style="font-family:Share Tech Mono,monospace;font-size:0.62em;'
        'color:#00CCFF;font-weight:700;letter-spacing:2px;margin-bottom:3px;">▶ TODAY</div>'
        if is_today else ""
    )

    return (
        f'<div class="{tile_class}">'
        + today_badge
        + f'<div class="forecast-day">{day["label"]}</div>'
        + f'<div style="font-size:1.9em;line-height:1.25;margin:4px 0;">{emoji}</div>'
        + f'<div class="forecast-hi">{hi}</div>'
        + f'<div class="forecast-lo">Low {lo}</div>'
        + f'<div style="font-size:0.76em;color:#7AACCC;margin:4px 0 2px;">{day.get("desc","--")}</div>'
        + f'<div style="background:rgba(255,255,255,0.07);border-radius:3px;height:5px;margin:5px 0;overflow:hidden;">'
        + f'<div style="height:100%;width:{pop}%;background:{pbc};border-radius:3px;"></div></div>'
        + f'<div class="small-muted">PoP {pop}%</div>'
        + f'<div class="small-muted">Rain: {precip_str}</div>'
        + f'<div class="small-muted">Wind: {day.get("wind",0)}/{day.get("gust",0)} mph</div>'
        + f'<div style="font-family:Share Tech Mono,monospace;font-size:0.58em;color:{src_color};margin-top:5px;">{src}</div>'
        + '</div>'
    )


def estimate_soil_moisture_belk(rain_30d, today_rain=0.0, profile="belk_compacted"):
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


def moon_phase_info(target_dt=None):
    if target_dt is None:
        target_dt = now_local()

    year = target_dt.year
    month = target_dt.month
    day = target_dt.day + (
        target_dt.hour / 24.0
        + target_dt.minute / 1440.0
        + target_dt.second / 86400.0
    )

    if month < 3:
        year -= 1
        month += 12

    month += 1
    c = 365.25 * year
    e = 30.6 * month
    jd = c + e + day - 694039.09
    jd /= 29.5305882
    frac = jd - int(jd)
    if frac < 0:
        frac += 1

    moon_age = frac * 29.5305882
    illumination = (1 - math.cos(2 * math.pi * frac)) / 2
    illumination_pct = round(illumination * 100, 1)

    if moon_age < 1.84566:
        phase_name = "NEW MOON"
    elif moon_age < 5.53699:
        phase_name = "WAXING CRESCENT"
    elif moon_age < 9.22831:
        phase_name = "FIRST QUARTER"
    elif moon_age < 12.91963:
        phase_name = "WAXING GIBBOUS"
    elif moon_age < 16.61096:
        phase_name = "FULL MOON"
    elif moon_age < 20.30228:
        phase_name = "WANING GIBBOUS"
    elif moon_age < 23.99361:
        phase_name = "LAST QUARTER"
    elif moon_age < 27.68493:
        phase_name = "WANING CRESCENT"
    else:
        phase_name = "NEW MOON"

    return illumination_pct, phase_name, round(moon_age, 1)

# ============================================================
# DATA FETCHERS — OBSERVATIONS
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
                if AMBIENT_STATION_NAME in (
                    d.get("info", {}).get("name", "") or ""
                ).lower()
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


@st.cache_data(ttl=60)
def fetch_usgs_gauges():
    results = {}
    for site_id, name in USGS_GAUGES.items():
        try:
            r = safe_get(
                "https://waterservices.usgs.gov/nwis/iv/",
                params={
                    "format": "json",
                    "sites": site_id,
                    "parameterCd": "00065,00060",
                },
                timeout=8,
            )
            r.raise_for_status()
            ts_list = r.json().get("value", {}).get("timeSeries", [])

            stage_ft = None
            discharge_cfs = None

            for ts in ts_list:
                code = ts["variable"]["variableCode"][0]["value"]
                vals = ts["values"][0]["value"]
                if not vals:
                    continue
                latest = vals[-1]["value"]
                try:
                    v = float(latest)
                    if code == "00065":
                        stage_ft = round(v, 2)
                    elif code == "00060":
                        discharge_cfs = round(v, 0)
                except (ValueError, TypeError):
                    pass

            if stage_ft is None and discharge_cfs is None:
                results[site_id] = {
                    "name": name, "stage_ft": None, "discharge_cfs": None,
                    "ok": False, "error": "No data returned",
                }
            else:
                results[site_id] = {
                    "name": name,
                    "stage_ft": stage_ft,
                    "discharge_cfs": discharge_cfs,
                    "ok": True,
                    "error": None,
                }
        except Exception as e:
            results[site_id] = {
                "name": name, "stage_ft": None, "discharge_cfs": None,
                "ok": False, "error": str(e),
            }
    return results


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
# DATA FETCHERS — FORECAST (3-SOURCE SMART CASCADE)
# ============================================================

@st.cache_data(ttl=900)
def fetch_nws_forecast():
    try:
        pts = safe_get(
            f"https://api.weather.gov/points/{LAT},{LON}",
            headers={"User-Agent": NWS_USER_AGENT},
            timeout=10,
        )
        pts.raise_for_status()
        forecast_url = pts.json()["properties"]["forecast"]

        fcast = safe_get(
            forecast_url,
            headers={"User-Agent": NWS_USER_AGENT},
            timeout=10,
        )
        fcast.raise_for_status()
        periods = fcast.json()["properties"]["periods"]

        date_periods = {}
        for p in periods:
            d = datetime.fromisoformat(p["startTime"]).date()
            if d not in date_periods:
                date_periods[d] = {}
            key = "day" if p["isDaytime"] else "night"
            date_periods[d][key] = p

        days = []
        for d in sorted(date_periods.keys())[:7]:
            dp = date_periods[d]
            day_p = dp.get("day")
            night_p = dp.get("night")

            if day_p is None and night_p is None:
                continue

            primary = day_p or night_p
            hi = day_p["temperature"] if day_p else None
            lo = night_p["temperature"] if night_p else None
            desc = primary["shortForecast"]

            day_pop = (day_p.get("probabilityOfPrecipitation") or {}).get("value") or 0 if day_p else 0
            night_pop = (night_p.get("probabilityOfPrecipitation") or {}).get("value") or 0 if night_p else 0
            pop = max(day_pop, night_pop)

            wind_str = primary.get("windSpeed", "0 mph")
            wind_nums = [int(x) for x in re.findall(r"\d+", wind_str)]
            wind = max(wind_nums) if wind_nums else 0

            dt = datetime.combine(d, datetime.min.time())
            days.append({
                "date": d.strftime("%Y-%m-%d"),
                "label": dt.strftime("%a %m/%d"),
                "hi": hi,
                "lo": lo,
                "precip": None,
                "pop": pop,
                "wind": wind,
                "gust": wind,
                "code": nws_desc_to_code(desc),
                "desc": desc[:22],
                "source": "NWS",
            })

        return ok_payload(data={"days": days}, source="NWS")
    except Exception as e:
        return ok_payload(data={"days": []}, source="NWS", error=str(e))


@st.cache_data(ttl=900)
def fetch_hrrr_open_meteo():
    try:
        r = safe_get(
            "https://api.open-meteo.com/v1/gfs",
            params={
                "latitude": LAT,
                "longitude": LON,
                "daily": (
                    "weathercode,temperature_2m_max,temperature_2m_min,"
                    "precipitation_sum,precipitation_probability_max,"
                    "windspeed_10m_max,windgusts_10m_max,winddirection_10m_dominant"
                ),
                "models": "hrrr_conus",
                "temperature_unit": "fahrenheit",
                "precipitation_unit": "inch",
                "windspeed_unit": "mph",
                "timezone": "America/New_York",
                "forecast_days": 3,
            },
            timeout=12,
        )
        r.raise_for_status()
        d = r.json().get("daily", {})

        days = []
        for i in range(min(3, len(d.get("time", [])))):
            dt = datetime.strptime(d["time"][i], "%Y-%m-%d")
            days.append({
                "date": d["time"][i],
                "label": dt.strftime("%a %m/%d"),
                "hi": round(d["temperature_2m_max"][i]) if d["temperature_2m_max"][i] is not None else None,
                "lo": round(d["temperature_2m_min"][i]) if d["temperature_2m_min"][i] is not None else None,
                "precip": round(d["precipitation_sum"][i] or 0, 2),
                "pop": int(round(d["precipitation_probability_max"][i] or 0)),
                "wind": round(d["windspeed_10m_max"][i] or 0),
                "gust": round(d["windgusts_10m_max"][i] or 0),
                "wind_dir": d["winddirection_10m_dominant"][i] or 0,
                "code": d["weathercode"][i] or 0,
                "desc": weather_desc(d["weathercode"][i] or 0),
                "source": "HRRR",
            })

        return ok_payload(data={"days": days}, source="HRRR")
    except Exception as e:
        return ok_payload(data={"days": []}, source="HRRR", error=str(e))


@st.cache_data(ttl=900)
def fetch_ecmwf_open_meteo():
    try:
        r = safe_get(
            "https://api.open-meteo.com/v1/ecmwf",
            params={
                "latitude": LAT,
                "longitude": LON,
                "daily": (
                    "weathercode,temperature_2m_max,temperature_2m_min,"
                    "precipitation_sum,precipitation_probability_max,"
                    "windspeed_10m_max,windgusts_10m_max,winddirection_10m_dominant"
                ),
                "temperature_unit": "fahrenheit",
                "precipitation_unit": "inch",
                "windspeed_unit": "mph",
                "timezone": "America/New_York",
                "forecast_days": 7,
            },
            timeout=12,
        )
        r.raise_for_status()
        d = r.json().get("daily", {})

        days = []
        for i in range(min(7, len(d.get("time", [])))):
            dt = datetime.strptime(d["time"][i], "%Y-%m-%d")
            days.append({
                "date": d["time"][i],
                "label": dt.strftime("%a %m/%d"),
                "hi": round(d["temperature_2m_max"][i]) if d["temperature_2m_max"][i] is not None else None,
                "lo": round(d["temperature_2m_min"][i]) if d["temperature_2m_min"][i] is not None else None,
                "precip": round(d["precipitation_sum"][i] or 0, 2),
                "pop": int(round(d["precipitation_probability_max"][i] or 0)),
                "wind": round(d["windspeed_10m_max"][i] or 0),
                "gust": round(d["windgusts_10m_max"][i] or 0),
                "wind_dir": d["winddirection_10m_dominant"][i] or 0,
                "code": d["weathercode"][i] or 0,
                "desc": weather_desc(d["weathercode"][i] or 0),
                "source": "ECMWF",
            })

        return ok_payload(data={"days": days}, source="ECMWF")
    except Exception as e:
        return ok_payload(data={"days": []}, source="ECMWF", error=str(e))


@st.cache_data(ttl=900)
def fetch_best_7day_forecast():
    nws_p = fetch_nws_forecast()
    hrrr_p = fetch_hrrr_open_meteo()
    ecmwf_p = fetch_ecmwf_open_meteo()

    nws_by_date   = {d["date"]: d for d in nws_p["data"].get("days", [])}
    hrrr_by_date  = {d["date"]: d for d in hrrr_p["data"].get("days", [])}
    ecmwf_by_date = {d["date"]: d for d in ecmwf_p["data"].get("days", [])}

    today = now_local().date()
    final_days = []

    for i in range(7):
        date_key = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        dt = datetime.strptime(date_key, "%Y-%m-%d")
        label = dt.strftime("%a %m/%d")

        hrrr  = hrrr_by_date.get(date_key)
        nws   = nws_by_date.get(date_key)
        ecmwf = ecmwf_by_date.get(date_key)

        if i <= 1 and hrrr:
            day = dict(hrrr)
            if nws:
                day["pop"] = max(hrrr["pop"], nws.get("pop", 0))
            day["source"] = "HRRR"
        elif i >= 4 and ecmwf:
            day = dict(ecmwf)
            if nws:
                day["pop"] = max(ecmwf["pop"], nws.get("pop", 0))
            day["source"] = "ECMWF"
        elif nws:
            day = dict(nws)
            if day.get("precip") is None:
                day["precip"] = (ecmwf or {}).get("precip") or (hrrr or {}).get("precip")
            day["source"] = "NWS"
        elif ecmwf:
            day = dict(ecmwf)
            day["source"] = "ECMWF"
        elif hrrr:
            day = dict(hrrr)
            day["source"] = "HRRR"
        else:
            day = {
                "date": date_key,
                "hi": None, "lo": None,
                "precip": None, "pop": 0,
                "wind": 0, "gust": 0,
                "code": 0, "desc": "Unavailable",
                "source": "N/A",
            }

        day["label"] = label
        final_days.append(day)

    errors = {}
    for p, name in [(nws_p, "NWS"), (hrrr_p, "HRRR"), (ecmwf_p, "ECMWF")]:
        if p.get("error"):
            errors[name] = p["error"]

    return ok_payload(data={"days": final_days, "errors": errors}, source="NWS/HRRR/ECMWF")

# ============================================================
# DATA FETCHERS — RADAR & ALERTS
# ============================================================

@st.cache_data(ttl=180)
def fetch_rainviewer_frames():
    try:
        r = safe_get("https://api.rainviewer.com/public/weather-maps.json", timeout=8)
        r.raise_for_status()
        data = r.json()
        return ok_payload(
            data={
                "past":    data.get("radar", {}).get("past", []),
                "nowcast": data.get("radar", {}).get("nowcast", []),
                "host":    data.get("host", "https://tilecache.rainviewer.com"),
            },
            source="RAINVIEWER",
        )
    except Exception as e:
        return ok_payload(
            data={"past": [], "nowcast": [], "host": "https://tilecache.rainviewer.com"},
            source="RAINVIEWER", error=str(e),
        )


@st.cache_data(ttl=120)
def fetch_nws_alerts():
    try:
        r = safe_get(
            "https://api.weather.gov/alerts/active",
            params={"point": f"{LAT},{LON}"},
            headers={"User-Agent": NWS_USER_AGENT},
            timeout=8,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
        alerts = []
        for f in features[:8]:
            props = f.get("properties", {})
            alerts.append({
                "event":    props.get("event", "Unknown"),
                "severity": props.get("severity", "Unknown"),
                "headline": (props.get("headline") or "")[:120],
                "geometry": f.get("geometry"),
            })
        return ok_payload(data={"alerts": alerts}, source="NWS ALERTS")
    except Exception as e:
        return ok_payload(data={"alerts": []}, source="NWS ALERTS", error=str(e))

# ============================================================
# RADAR MAP BUILDER
# ============================================================

def build_radar_folium_map(rv_payload, alerts_payload):
    past      = rv_payload.get("data", {}).get("past", [])
    nowcast   = rv_payload.get("data", {}).get("nowcast", [])
    host      = rv_payload.get("data", {}).get("host", "https://tilecache.rainviewer.com")
    all_frames = past + nowcast
    len_past   = len(past)
    alerts     = alerts_payload.get("data", {}).get("alerts", [])

    m = folium.Map(
        location=[LAT, LON],
        zoom_start=8,
        tiles=None,
        control_scale=True,
        prefer_canvas=True,
    )

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        attr="© OpenStreetMap · © CARTO",
        name="Dark",
        max_zoom=19,
    ).add_to(m)

    folium.CircleMarker(
        location=[LAT, LON],
        radius=10,
        color="#00FFFF", weight=2,
        fill=True, fill_color="#00FFFF", fill_opacity=0.88,
        tooltip="<b>WCU Belk Building</b><br>Cullowhee, NC · 35.307°N | 83.183°W",
        popup=folium.Popup("<b>WCU Belk Building</b><br>Cullowhee, NC", max_width=200),
    ).add_to(m)

    folium.Marker(
        location=[LAT + 0.025, LON + 0.01],
        icon=folium.DivIcon(
            html=(
                '<div style="font-family:Share Tech Mono,monospace;font-size:11px;'
                'color:#00FFFF;font-weight:700;white-space:nowrap;'
                'text-shadow:0 0 8px rgba(0,50,100,0.95);">WCU BELK</div>'
            ),
            icon_size=(80, 16), icon_anchor=(40, 8),
        ),
    ).add_to(m)

    sev_color = {
        "Extreme":  "#FF3333",
        "Severe":   "#FF8C00",
        "Moderate": "#FFD700",
        "Minor":    "#AAFF00",
        "Unknown":  "#7AACCC",
    }
    for alert in alerts:
        geom = alert.get("geometry")
        if not geom:
            continue
        ec      = sev_color.get(alert.get("severity", "Unknown"), "#7AACCC")
        event   = alert.get("event", "Alert")
        headline= alert.get("headline", "")

        def _poly(ring, _ec=ec, _ev=event, _hl=headline):
            folium.Polygon(
                locations=[[pt[1], pt[0]] for pt in ring],
                color=_ec, weight=2,
                fill=True, fill_color=_ec, fill_opacity=0.13,
                tooltip=f"<b>{_ev}</b><br>{_hl[:80]}",
                popup=folium.Popup(f"<b>{_ev}</b><br>{_hl}", max_width=300),
            ).add_to(m)

        if geom["type"] == "Polygon":
            _poly(geom["coordinates"][0])
        elif geom["type"] == "MultiPolygon":
            for poly in geom["coordinates"]:
                _poly(poly[0])

    if all_frames:
        map_var  = m.get_name()
        frames_j = json.dumps(all_frames)
        max_idx  = len(all_frames) - 1

        radar_js = f"""
<script>
(function() {{
    function initRadar() {{
        var map = window['{map_var}'];
        if (!map) {{ setTimeout(initRadar, 250); return; }}

        var frames    = {frames_j};
        var host      = "{host}";
        var lenPast   = {len_past};
        var color     = 2;
        var smooth    = 1;
        var RDR_OPACITY = 0.65;
        var idx       = frames.length - 1;
        var layers    = {{}};
        var playing   = true;
        var timer     = null;

        frames.forEach(function(f) {{
            var url = host + f.path + '/256/{{z}}/{{x}}/{{y}}/' + color + '/' + smooth + '_1.png';
            layers[f.time] = L.tileLayer(url, {{ opacity: 0, zIndex: 200 }});
            layers[f.time].addTo(map);
        }});

        function showFrame(i) {{
            frames.forEach(function(f, j) {{
                layers[f.time].setOpacity(j === i ? RDR_OPACITY : 0);
            }});
            idx = i;

            var ts  = new Date(frames[i].time * 1000);
            var lbl = ts.toLocaleTimeString([], {{hour:'2-digit', minute:'2-digit'}});
            var el  = document.getElementById('rv-ts');
            if (el) el.innerText = lbl;

            var badge = document.getElementById('rv-badge');
            if (badge) {{
                var isNow = i >= lenPast;
                badge.innerText = isNow ? '🔮 NOWCAST' : '📡 NEXRAD';
                badge.style.color = isNow ? '#AAFF00' : '#00FFCC';
            }}

            var prog = document.getElementById('rv-prog');
            if (prog && prog !== document.activeElement) prog.value = i;
        }}

        function nextFrame() {{ showFrame((idx + 1) % frames.length); }}

        function startAnim() {{
            if (timer) clearInterval(timer);
            timer = setInterval(nextFrame, 550);
            playing = true;
            var btn = document.getElementById('rv-btn');
            if (btn) btn.innerText = '⏸';
        }}

        function stopAnim() {{
            if (timer) {{ clearInterval(timer); timer = null; }}
            playing = false;
            var btn = document.getElementById('rv-btn');
            if (btn) btn.innerText = '▶';
        }}

        window._rvToggle = function() {{ playing ? stopAnim() : startAnim(); }};
        window._rvSeek   = function(v) {{ stopAnim(); showFrame(parseInt(v)); }};

        var ctrl = L.control({{position: 'bottomleft'}});
        ctrl.onAdd = function() {{
            var d = L.DomUtil.create('div', '');
            d.innerHTML =
                '<div style="background:rgba(6,12,20,0.93);border:1px solid rgba(0,136,255,0.4);' +
                'border-radius:8px;padding:8px 14px;font-family:monospace;font-size:11px;' +
                'color:#7AACCC;min-width:280px;box-shadow:0 0 22px rgba(0,60,140,0.45);">' +
                '<div style="display:flex;align-items:center;gap:8px;margin-bottom:7px;">' +
                '<span id="rv-badge" style="color:#00FFCC;font-weight:700;font-size:12px;">📡 NEXRAD</span>' +
                '<span style="color:#2A4060;">|</span>' +
                '<span id="rv-ts" style="color:#FFFFFF;font-size:12px;letter-spacing:1px;">--:--</span>' +
                '<span style="flex:1;"></span>' +
                '<button onclick="window._rvToggle()" id="rv-btn" style="' +
                'background:rgba(0,136,255,0.2);border:1px solid rgba(0,136,255,0.5);' +
                'border-radius:4px;color:#00FFCC;cursor:pointer;padding:2px 10px;font-size:14px;">⏸</button>' +
                '</div>' +
                '<input id="rv-prog" type="range" min="0" max="{max_idx}" value="{max_idx}"' +
                ' oninput="window._rvSeek(this.value)"' +
                ' style="width:100%;accent-color:#00FFCC;cursor:pointer;">' +
                '<div style="display:flex;justify-content:space-between;font-size:9px;' +
                'color:#2A5070;margin-top:3px;">' +
                '<span>◀ PAST</span><span>LIVE</span><span style="color:#AAFF00;">NOWCAST ▶</span>' +
                '</div>' +
                '<div style="font-size:9px;color:#1A3050;margin-top:4px;text-align:right;">' +
                'RainViewer · NWS Alerts</div>' +
                '</div>';
            L.DomEvent.disableClickPropagation(d);
            L.DomEvent.disableScrollPropagation(d);
            return d;
        }};
        ctrl.addTo(map);

        showFrame(frames.length - 1);
        startAnim();
    }}
    setTimeout(initRadar, 400);
}})();
</script>
"""
        m.get_root().html.add_child(folium.Element(radar_js))

    return m

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
# LOAD ALL DATA
# ============================================================

with st.spinner("Syncing all data sources..."):
    ambient_payload  = fetch_ambient()
    blitz_payload    = fetch_blitzortung_lightning()
    aqi_payload      = fetch_aqi()
    airport_payload  = fetch_airport_metar()
    usgs_data        = fetch_usgs_gauges()
    forecast_payload = fetch_best_7day_forecast()
    hist_payload     = fetch_historical_rain_30d()
    rv_payload       = fetch_rainviewer_frames()
    alerts_payload   = fetch_nws_alerts()

ambient      = ambient_payload.get("data", {})
aqi_data     = aqi_payload.get("data", {})
airport      = airport_payload.get("data", {})
forecast     = forecast_payload.get("data", {}).get("days", [])
hist_rain    = hist_payload.get("data", {}).get("rain_30d", [0.05] * 30)

# ============================================================
# DERIVED METRICS
# ============================================================

rain_today = 0.0
if ambient_payload["ok"]:
    rain_today = ambient.get("rain_today", 0.0) or 0.0
elif airport_payload["ok"]:
    rain_today = airport.get("precip", 0.0) or 0.0

rain_3d_forecast = sum((day.get("precip") or 0.0) for day in forecast[:3]) if forecast else 0.0
wind_now   = first_non_none(ambient.get("wind_speed"), airport.get("wind_mph"), 0) or 0
pop_today  = forecast[0]["pop"] if forecast else 0
today_src  = forecast[0].get("source", "CASCADE") if forecast else "CASCADE"

soil_pct, soil_status, soil_color, soil_storage = estimate_soil_moisture_belk(
    hist_rain, rain_today, profile="belk_compacted"
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
    l_color   = COLORS["green"]
    l_label   = "NO STRIKES"
    l_detail  = "No lightning detected nearby"

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

temp_now     = first_non_none(ambient.get("temp"), airport.get("temp_f"))
temp_display = clamp(temp_now if temp_now is not None else 70, 0, 120)
temp_color   = (
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

tonight_low   = forecast[0]["lo"] if forecast and forecast[0].get("lo") is not None else 50
freeze_display = clamp(tonight_low, 0, 80)
freeze_color  = (
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

moon_pct, moon_name, moon_age = moon_phase_info(now_local())
moon_color = (
    COLORS["white"] if moon_pct >= 90 else
    COLORS["orange"] if moon_pct >= 65 else
    COLORS["yellow"] if moon_pct >= 40 else
    COLORS["green"] if moon_pct >= 15 else
    COLORS["blue"]
)

aqi_val     = aqi_data.get("aqi", 0) if aqi_payload["ok"] else 0
aqi_display = min(aqi_val, 200)
aqi_color   = (
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
rain3d_color   = (
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

pressure_now  = first_non_none(ambient.get("pressure"), 29.92)
current_time  = now_local()
tz_label      = current_time.tzname() or "ET"

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
        {render_source_status_badge("🌐 FORECAST", bool(forecast))}
        {render_source_status_badge("🗺️ RADAR", rv_payload["ok"])}
    </div>
</div>
""",
    unsafe_allow_html=True,
)

# ============================================================
# GAUGE ROW 1 — Hazard & Atmospheric
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
# GAUGE ROW 2 — Site Conditions
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
        "detail": f"Source: <b style='color:#00FFCC'>{today_src}</b>",
        "source": today_src,
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
        "source": "NWS/HRRR/ECMWF",
    },
    {
        "title": "MOON PHASE",
        "value": moon_pct,
        "min_val": 0,
        "max_val": 100,
        "unit": "%",
        "thresholds": GAUGE_THRESHOLDS["moon"],
        "color": moon_color,
        "label": moon_name,
        "detail": f"Moon age: <b style='color:#00FFCC'>{moon_age} days</b>",
        "source": "ASTRONOMICAL CALC",
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
# GAUGE ROW 3 — Rain & Short-Term
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
        "source": "HRRR→NWS→ECMWF",
    },
    {
        "title": "PRESSURE",
        "value": clamp(pressure_now or 29.92, 28, 32),
        "min_val": 28,
        "max_val": 32,
        "unit": " inHg",
        "thresholds": [
            {"range": [28, 29],     "color": "rgba(255,51,51,0.12)"},
            {"range": [29, 29.8],   "color": "rgba(255,140,0,0.12)"},
            {"range": [29.8, 30.3], "color": "rgba(0,255,156,0.12)"},
            {"range": [30.3, 32],   "color": "rgba(90,200,250,0.12)"},
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
# 7-DAY FORECAST PANEL
# ============================================================

st.markdown(
    '<div class="panel"><div class="panel-title">'
    '📅 Seven-Day Forecast — HRRR (0-1d) · NWS (2-3d) · ECMWF (4-6d)'
    '</div>',
    unsafe_allow_html=True,
)

if forecast:
    tiles_html = '<div class="forecast-row">'
    for i, day in enumerate(forecast):
        tiles_html += build_forecast_tile(day, is_today=(i == 0))
    tiles_html += '</div>'
    st.markdown(tiles_html, unsafe_allow_html=True)
else:
    st.warning("Forecast data unavailable.")

st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# RADAR MAP PANEL
# ============================================================

n_frames  = (
    len(rv_payload.get("data", {}).get("past", []))
    + len(rv_payload.get("data", {}).get("nowcast", []))
)
n_alerts  = len(alerts_payload.get("data", {}).get("alerts", []))
rv_live   = "LIVE" if rv_payload["ok"] else "OFFLINE"
al_live   = "LIVE" if alerts_payload["ok"] else "OFFLINE"

st.markdown(
    f'<div class="panel"><div class="panel-title">'
    f'🗺️ Live Radar Map — NEXRAD Composite · NWS Storm Alerts'
    f'&nbsp;<span class="source-badge">RAINVIEWER {rv_live}</span>'
    f'<span class="source-badge">NWS ALERTS {al_live}</span>'
    f'<span class="source-badge">{n_frames} FRAMES</span>'
    f'<span class="source-badge">{n_alerts} ACTIVE ALERTS</span>'
    f'</div>',
    unsafe_allow_html=True,
)

radar_map = build_radar_folium_map(rv_payload, alerts_payload)
st_folium(radar_map, use_container_width=True, height=540, returned_objects=[])

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
        render_data_card("Moon Phase", moon_name, f"Illumination: {moon_pct}%")
        render_data_card("Lightning Distance", format_num(l_dist, 1, " mi"), l_source)
        render_data_card("AQI", format_num(aqi_val, 0, ""), "Open-Meteo AQI")

    st.markdown("</div>", unsafe_allow_html=True)

with right:
    st.markdown('<div class="panel"><div class="panel-title">💧 USGS / Aviation / Diagnostics</div>', unsafe_allow_html=True)

    for site_id, item in usgs_data.items():
        status   = "LIVE" if item["ok"] else "OFFLINE"
        stage    = item.get("stage_ft")
        cfs      = item.get("discharge_cfs")
        if item["ok"]:
            val_str    = f"{stage} ft" if stage is not None else "--"
            detail_str = f"{int(cfs):,} cfs • Gauge {site_id} • {status}" if cfs is not None else f"Gauge {site_id} • {status}"
        else:
            val_str    = "OFFLINE"
            detail_str = f"Gauge {site_id} • {item.get('error','')[:60]}"
        render_data_card(f"USGS {item['name']}", val_str, detail_str)

    render_data_card("Airport METAR", AIRPORT_ID, airport.get("raw", "No raw observation"))
    render_data_card(
        "Forecast Cascade",
        "HRRR · NWS · ECMWF",
        "Days 0-1 HRRR | Days 2-3 NWS | Days 4-6 ECMWF",
    )
    render_data_card("Soil Wetness Profile", "belk_compacted", "Compacted campus-ground assumption")

    if (
        ambient_payload["error"] or blitz_payload["error"] or
        aqi_payload["error"] or airport_payload["error"] or hist_payload["error"]
    ):
        st.markdown("<hr class='soft'>", unsafe_allow_html=True)
        st.markdown("<div class='data-card-title'>Diagnostics</div>", unsafe_allow_html=True)
        for payload in [ambient_payload, blitz_payload, aqi_payload, airport_payload, hist_payload]:
            if payload.get("error"):
                st.markdown(
                    f"<div class='small-muted'><b>{payload.get('source')}:</b> {payload.get('error')}</div>",
                    unsafe_allow_html=True,
                )

    forecast_errors = forecast_payload.get("data", {}).get("errors", {})
    if forecast_errors:
        st.markdown("<hr class='soft'>", unsafe_allow_html=True)
        st.markdown("<div class='data-card-title'>Forecast Model Diagnostics</div>", unsafe_allow_html=True)
        for model_name, err in forecast_errors.items():
            st.markdown(
                f"<div class='small-muted'><b>{model_name}:</b> {err}</div>",
                unsafe_allow_html=True,
            )

    st.markdown("</div>", unsafe_allow_html=True)
