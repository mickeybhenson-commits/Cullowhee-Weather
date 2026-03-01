import streamlit as st
import requests
import json
import math
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from streamlit_autorefresh import st_autorefresh

st.set_page_config(
    page_title="Cullowhee Weather Intelligence",
    layout="wide"
)
st_autorefresh(interval=300000, key="refresh")

LAT = 35.3079
LON = -83.1746
SITE = "NCCAT — Cullowhee, NC"

AMBIENT_API_KEY = st.secrets.get("AMBIENT_API_KEY", "9ed066cb260c42adbe8778e0afb09e747f8450a7dd20479791a18d692b722334")
AMBIENT_APP_KEY = st.secrets.get("AMBIENT_APP_KEY", "9ed066cb260c42adbe8778e0afb09e747f8450a7dd20479791a18d692b722334")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600;700&family=Share+Tech+Mono&display=swap');
html, body, .stApp {
    background-color: #060C14;
    color: #E0E8F0;
    font-family: 'Rajdhani', sans-serif;
}
h1, h2, h3 { font-family: 'Rajdhani', sans-serif; letter-spacing: 2px; }
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
section.main > div { position: relative; z-index: 1; }
.site-header {
    border-left: 6px solid #0088FF;
    padding: 12px 20px;
    margin-bottom: 24px;
    background: rgba(0,136,255,0.06);
    border-radius: 0 8px 8px 0;
}
.site-title { font-size: 2.8em; font-weight: 700; color: #FFFFFF; margin: 0; letter-spacing: 3px; }
.site-sub { font-size: 1.1em; color: #7AACCC; text-transform: uppercase; font-family: 'Share Tech Mono', monospace; }
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
.stMetric label { font-family: 'Share Tech Mono', monospace !important; font-size: 0.75em !important; color: #7AACCC !important; }
.stMetric [data-testid="metric-container"] { background: rgba(0,136,255,0.05); border-radius: 8px; padding: 8px; border: 1px solid rgba(0,136,255,0.15); }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
#  HELPER FUNCTIONS
# ─────────────────────────────────────────────
def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def calc_feels_like(temp_f, humidity, wind_mph):
    if temp_f is None: return None
    humidity = humidity or 0
    wind_mph = wind_mph or 0
    if temp_f >= 80 and humidity >= 40:
        hi = (-42.379 + 2.04901523*temp_f + 10.14333127*humidity
              - 0.22475541*temp_f*humidity - 0.00683783*temp_f**2
              - 0.05481717*humidity**2 + 0.00122874*temp_f**2*humidity
              + 0.00085282*temp_f*humidity**2 - 0.00000199*temp_f**2*humidity**2)
        return round(hi, 1)
    elif temp_f <= 50 and wind_mph > 3:
        wc = 35.74 + 0.6215*temp_f - 35.75*(wind_mph**0.16) + 0.4275*temp_f*(wind_mph**0.16)
        return round(wc, 1)
    return round(temp_f, 1)

def calc_dewpoint_f(temp_f, humidity):
    if temp_f is None or humidity is None or humidity <= 0: return None
    temp_c = (temp_f - 32) * 5/9
    a, b = 17.625, 243.04
    try:
        alpha = math.log(humidity / 100.0) + (a * temp_c) / (b + temp_c)
        dp_c = (b * alpha) / (a - alpha)
        return round(dp_c * 9/5 + 32, 1)
    except:
        return None

def calc_fog_spread(temp_f, dewpoint_f):
    if temp_f is None or dewpoint_f is None: return None
    return round(temp_f - dewpoint_f, 1)

# ─────────────────────────────────────────────
#  DATA FETCHERS
# ─────────────────────────────────────────────

@st.cache_data(ttl=300)
def fetch_ambient():
    try:
        r = requests.get(
            "https://api.ambientweather.net/v1/devices",
            params={"apiKey": AMBIENT_API_KEY, "applicationKey": AMBIENT_APP_KEY},
            timeout=10
        )
        r.raise_for_status()
        devices = r.json()
        if devices:
            target = next(
                (d for d in devices if d.get("macAddress","").replace(":","").replace("-","").lower() == "35c7b0accb75a84d7891d82f125001a8"),
                devices[0]
            )
            last = target.get("lastData", {})
            return {
                "temp":           last.get("tempf"),
                "humidity":       last.get("humidity"),
                "wind_speed":     last.get("windspeedmph", 0),
                "wind_dir":       last.get("winddir", 0),
                "wind_gust":      last.get("windgustmph", 0),
                "rain_today":     last.get("dailyrainin", 0.0),
                "rain_1hr":       last.get("hourlyrainin", 0.0),
                "rain_week":      last.get("weeklyrainin", 0.0),
                "rain_month":     last.get("monthlyrainin", 0.0),
                "pressure":       last.get("baromrelin"),
                "uv":             last.get("uv", 0),
                "solar":          last.get("solarradiation", 0),
                "lightning_dist": last.get("lightning_distance"),
                "lightning_day":  last.get("lightning_day", 0),
                "lightning_hour": last.get("lightning_hour", 0),
                "name":           target.get("info", {}).get("name", "Riverbend on the Tuckasegee"),
                "ok": True
            }
    except:
        pass
    return {"ok": False}

@st.cache_data(ttl=60)
def fetch_blitzortung_lightning():
    try:
        now = datetime.utcnow()
        closest_dist = None
        for minutes_back in range(0, 31):
            t = now - timedelta(minutes=minutes_back)
            url = (
                f"https://data.blitzortung.org/Data/Protected/By_Location/"
                f"By_Region/America/Strokes/"
                f"{t.year}/{t.month:02d}/{t.day:02d}/"
                f"{t.hour:02d}/{t.minute:02d}.json"
            )
            try:
                r = requests.get(url, timeout=4)
                if r.status_code == 200 and r.text.strip():
                    strokes = r.json()
                    for stroke in strokes:
                        slat = stroke.get("lat") or stroke.get("y")
                        slon = stroke.get("lon") or stroke.get("x")
                        if slat is not None and slon is not None:
                            dist = haversine_miles(LAT, LON, float(slat), float(slon))
                            if closest_dist is None or dist < closest_dist:
                                closest_dist = dist
            except:
                continue
        if closest_dist is not None:
            return {"dist": round(closest_dist, 1), "ok": True}
    except:
        pass
    return {"ok": False}

def resolve_lightning(ambient, blitz):
    awn_dist = None
    blitz_dist = None
    if ambient.get("ok") and ambient.get("lightning_dist") is not None:
        awn_dist = float(ambient["lightning_dist"])
    if blitz.get("ok") and blitz.get("dist") is not None:
        blitz_dist = float(blitz["dist"])
    if awn_dist is not None and blitz_dist is not None:
        final_dist = min(awn_dist, blitz_dist)
        source_tag = "AWN + BLITZ"
    elif awn_dist is not None:
        final_dist = awn_dist
        source_tag = "AWN ONLY"
    elif blitz_dist is not None:
        final_dist = blitz_dist
        source_tag = "BLITZ ONLY"
    else:
        final_dist = 25.0
        source_tag = "NO DATA"
    strikes = ambient.get("lightning_day", 0) if ambient.get("ok") else "--"
    return round(final_dist, 1), source_tag, strikes

@st.cache_data(ttl=1800)
def fetch_aqi():
    try:
        r = requests.get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={
                "latitude": LAT, "longitude": LON,
                "hourly": "us_aqi",
                "timezone": "America/New_York",
                "forecast_days": 1
            },
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        aqi_vals = data["hourly"]["us_aqi"]
        now_hour = datetime.now().hour
        aqi = next((v for v in [aqi_vals[now_hour] if now_hour < len(aqi_vals) else None] + aqi_vals if v is not None), 0)
        return {"aqi": int(aqi), "ok": True}
    except:
        return {"ok": False}

@st.cache_data(ttl=300)
def fetch_airport_metar():
    try:
        r = requests.get(
            "https://aviationweather.gov/api/data/metar",
            params={"ids": "K24A", "format": "json"},
            timeout=8
        )
        r.raise_for_status()
        data = r.json()
        if data:
            obs = data[0]
            return {
                "temp_f":   round(obs.get("temp", 0) * 9/5 + 32, 1) if obs.get("temp") is not None else None,
                "wind_mph": round(obs.get("wspd", 0) * 1.15078, 1) if obs.get("wspd") else None,
                "wind_dir": obs.get("wdir"),
                "altim":    obs.get("altim"),
                "precip":   obs.get("precip", 0.0),
                "cover":    obs.get("skyCondition", [{}])[0].get("skyCover", "CLR") if obs.get("skyCondition") else "CLR",
                "raw":      obs.get("rawOb", ""),
                "time":     obs.get("obsTime", ""),
                "ok": True
            }
    except:
        pass
    return {"ok": False}

@st.cache_data(ttl=300)
def fetch_usgs_rain():
    results = {}
    gauges = {
        "03439000": "Tuckasegee @ Cullowhee",
        "03460000": "Tuckasegee @ Bryson City"
    }
    for site_id, name in gauges.items():
        try:
            r = requests.get(
                "https://waterservices.usgs.gov/nwis/iv/",
                params={"format": "json", "sites": site_id, "parameterCd": "00045"},
                timeout=8
            )
            r.raise_for_status()
            data = r.json()
            val = float(data['value']['timeSeries'][0]['values'][0]['value'][0]['value'])
            results[site_id] = {"name": name, "value": val, "ok": True}
        except:
            results[site_id] = {"name": name, "value": 0.0, "ok": False}
    return results

@st.cache_data(ttl=600)
def fetch_multimodel_forecast():
    model_params = {"hrrr": "hrrr_conus", "gfs": "gfs_seamless"}
    base_params = {
        "latitude": LAT, "longitude": LON,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weathercode,windspeed_10m_max",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "windspeed_unit": "mph",
        "timezone": "America/New_York",
        "forecast_days": 7
    }
    forecasts = {}
    for model_key, model_str in model_params.items():
        try:
            p = {**base_params, "models": model_str}
            r = requests.get("https://api.open-meteo.com/v1/forecast", params=p, timeout=12)
            r.raise_for_status()
            forecasts[model_key] = r.json()["daily"]
        except:
            forecasts[model_key] = None

    days = []
    today = datetime.now()
    for i in range(7):
        date = today + timedelta(days=i)
        primary = "hrrr" if i <= 1 else "gfs"
        secondary = "gfs" if i <= 1 else "hrrr"
        src = forecasts.get(primary) or forecasts.get(secondary)
        model_label = primary.upper() if forecasts.get(primary) else secondary.upper()
        if src and i < len(src.get("time", [])):
            days.append({
                "date":   date.strftime("%a %m/%d"),
                "day":    date.strftime("%a"),
                "hi":     round(src["temperature_2m_max"][i] or 0),
                "lo":     round(src["temperature_2m_min"][i] or 0),
                "precip": round(src["precipitation_sum"][i] or 0, 2),
                "pop":    src["precipitation_probability_max"][i] or 0,
                "wind":   round(src["windspeed_10m_max"][i] or 0),
                "code":   src["weathercode"][i] or 0,
                "model":  model_label,
                "desc":   weather_desc(src["weathercode"][i] or 0)
            })
        else:
            days.append({"date": date.strftime("%a %m/%d"), "day": date.strftime("%a"),
                         "hi": 60, "lo": 40, "precip": 0.0, "pop": 10, "wind": 10,
                         "code": 0, "model": "N/A", "desc": "Unknown"})
    return days

@st.cache_data(ttl=3600)
def fetch_historical_rain_30d():
    try:
        end = datetime.now()
        start = end - timedelta(days=30)
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": LAT, "longitude": LON,
                "daily": "precipitation_sum",
                "precipitation_unit": "inch",
                "timezone": "America/New_York",
                "start_date": start.strftime("%Y-%m-%d"),
                "end_date": end.strftime("%Y-%m-%d")
            },
            timeout=12
        )
        r.raise_for_status()
        vals = r.json()["daily"]["precipitation_sum"]
        return [v or 0.0 for v in vals]
    except:
        return [0.05] * 30

def weather_desc(code):
    codes = {
        0:"Clear", 1:"Mainly Clear", 2:"Partly Cloudy", 3:"Overcast",
        45:"Foggy", 48:"Rime Fog", 51:"Lt Drizzle", 53:"Drizzle",
        55:"Heavy Drizzle", 61:"Lt Rain", 63:"Rain", 65:"Heavy Rain",
        71:"Lt Snow", 73:"Snow", 75:"Heavy Snow", 80:"Rain Showers",
        81:"Mod Showers", 82:"Heavy Showers", 95:"Thunderstorm",
        96:"Tstm+Hail", 99:"Tstm+Heavy Hail"
    }
    return codes.get(code, "Unknown")

def pop_color(pop):
    if pop < 20: return "#00FF9C"
    if pop < 40: return "#AAFF00"
    if pop < 60: return "#FFD700"
    if pop < 80: return "#FF8C00"
    return "#FF3333"

def estimate_soil_moisture(rain_30d, today_rain=0.0):
    FIELD_CAPACITY = 2.16
    WILTING_POINT  = 1.80
    MAX_STORAGE    = FIELD_CAPACITY + 0.5
    monthly_et = {1:0.04, 2:0.06, 3:0.10, 4:0.14, 5:0.17,
                  6:0.20, 7:0.21, 8:0.19, 9:0.14, 10:0.09,
                  11:0.05, 12:0.03}
    storage = FIELD_CAPACITY * 0.6
    today_month = datetime.now().month
    start_month = (today_month - 1) or 12
    for i, rain in enumerate(rain_30d):
        month = start_month if i < 15 else today_month
        et_daily = monthly_et.get(month, 0.10)
        storage = storage + rain - et_daily
        storage = max(WILTING_POINT, min(MAX_STORAGE, storage))
    storage = min(MAX_STORAGE, storage + today_rain)
    pct = ((storage - WILTING_POINT) / (MAX_STORAGE - WILTING_POINT)) * 100
    pct = max(0, min(100, pct))
    if pct >= 90:   status, color = "SATURATED", "#FF3333"
    elif pct >= 75: status, color = "WET",        "#FF8C00"
    elif pct >= 50: status, color = "MOIST",      "#FFD700"
    elif pct >= 25: status, color = "ADEQUATE",   "#00FF9C"
    else:           status, color = "DRY",         "#5AC8FA"
    return round(pct, 1), status, color, round(storage, 2)

def make_gauge(value, title, min_val=0, max_val=100, unit="%", thresholds=None, color=None):
    if thresholds is None:
        thresholds = [
            {"range":[0,25],  "color":"rgba(0,255,156,0.15)"},
            {"range":[25,50], "color":"rgba(255,215,0,0.15)"},
            {"range":[50,75], "color":"rgba(255,140,0,0.15)"},
            {"range":[75,100],"color":"rgba(255,51,51,0.15)"},
        ]
    if color is None:
        if value < 30:   color = "#00FF9C"
        elif value < 55: color = "#FFD700"
        elif value < 75: color = "#FF8C00"
        else:            color = "#FF3333"
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        number={"suffix": unit, "font": {"size": 26, "color": "#FFFFFF", "family": "Rajdhani"}},
        title={"text": title, "font": {"size": 11, "color": "#7AACCC", "family": "Share Tech Mono"}},
        gauge={
            "axis": {"range": [min_val, max_val], "tickwidth": 1, "tickcolor": "#2A4060",
                     "tickfont": {"color": "#5A7A9A", "size": 8}},
            "bar": {"color": color, "thickness": 0.25},
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 0,
            "steps": thresholds,
            "threshold": {"line": {"color": color, "width": 3}, "thickness": 0.85, "value": value}
        }
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=35, b=5, l=15, r=15), height=185,
        font={"color": "#E0E8F0"}
    )
    return fig

def sublabel(text, color="#7AACCC"):
    return f"<div style='text-align:center;font-family:Rajdhani;font-size:1.2em;font-weight:700;color:{color};margin-top:2px;'>{text}</div>"

def subsub(text, color="#7AACCC"):
    return f"<div style='text-align:center;font-family:Rajdhani;font-size:0.85em;color:{color};'>{text}</div>"

def srctag(text):
    return f"<div style='text-align:center;font-family:Share Tech Mono,monospace;font-size:0.62em;color:#2A6080;margin-top:1px;'>SRC: {text}</div>"

# ─────────────────────────────────────────────
#  FETCH ALL DATA
# ─────────────────────────────────────────────
with st.spinner("Syncing all data sources..."):
    ambient   = fetch_ambient()
    blitz     = fetch_blitzortung_lightning()
    aqi_data  = fetch_aqi()
    airport   = fetch_airport_metar()
    usgs      = fetch_usgs_rain()
    forecast  = fetch_multimodel_forecast()
    hist_rain = fetch_historical_rain_30d()

rain_today = 0.0
if ambient.get("ok"):
    rain_today = ambient.get("rain_today", 0.0) or 0.0
elif airport.get("ok") and airport.get("precip"):
    rain_today = airport["precip"]

rain_3d_forecast = sum(d["precip"] for d in forecast[:3])
wind_now  = ambient.get("wind_speed", 0) or (airport.get("wind_mph") or 0)
pop_today = forecast[0]["pop"] if forecast else 0

soil_pct, soil_status, soil_color, soil_storage = estimate_soil_moisture(hist_rain, rain_today)

# Lightning
l_dist, l_source, l_strikes = resolve_lightning(ambient, blitz)
l_display = min(l_dist, 25)
l_color = "#FF3333" if l_dist < 5 else "#FF8C00" if l_dist < 10 else "#FFD700" if l_dist < 15 else "#00FF9C"
l_label  = "CRITICAL" if l_dist < 5 else "NEARBY" if l_dist < 10 else "MODERATE" if l_dist < 15 else "CLEAR"

# UV Index
uv_val = ambient.get("uv", 0) if ambient.get("ok") else 0
uv_val = uv_val or 0
uv_color = "#00FF9C" if uv_val <= 2 else "#AAFF00" if uv_val <= 5 else "#FFD700" if uv_val <= 7 else "#FF8C00" if uv_val <= 10 else "#FF3333"
uv_label = "LOW" if uv_val <= 2 else "MODERATE" if uv_val <= 5 else "HIGH" if uv_val <= 7 else "VERY HIGH" if uv_val <= 10 else "EXTREME"

# Feels Like
temp_now = ambient.get("temp") if ambient.get("ok") else None
hum_now  = ambient.get("humidity") if ambient.get("ok") else None
fl_val   = calc_feels_like(temp_now, hum_now, wind_now)
fl_display = max(0, min(120, fl_val)) if fl_val is not None else 70
fl_color = "#5AC8FA" if (fl_val or 70) < 32 else "#00FFFF" if (fl_val or 70) < 50 else "#00FF9C" if (fl_val or 70) < 80 else "#FFD700" if (fl_val or 70) < 95 else "#FF8C00" if (fl_val or 70) < 105 else "#FF3333"
fl_label = "FREEZING" if (fl_val or 70) < 32 else "COLD" if (fl_val or 70) < 50 else "COMFORTABLE" if (fl_val or 70) < 80 else "HOT" if (fl_val or 70) < 95 else "VERY HOT" if (fl_val or 70) < 105 else "DANGEROUS"

# Humidity
hum_val = hum_now or 0
hum_color = "#5AC8FA" if hum_val < 30 else "#00FF9C" if hum_val < 60 else "#FFD700" if hum_val < 80 else "#FF8C00"
hum_label = "DRY" if hum_val < 30 else "COMFORTABLE" if hum_val < 60 else "HUMID" if hum_val < 80 else "VERY HUMID"

# Freeze Risk
tonight_low = forecast[0]["lo"] if forecast else 50
freeze_color = "#00FF9C" if tonight_low > 45 else "#FFD700" if tonight_low > 32 else "#FF8C00" if tonight_low > 28 else "#FF3333"
freeze_label = "NO RISK" if tonight_low > 45 else "WATCH" if tonight_low > 32 else "FREEZE" if tonight_low > 28 else "HARD FREEZE"
freeze_display = max(0, min(80, tonight_low))

# Fog Index
dp_val     = calc_dewpoint_f(temp_now, hum_now)
fog_spread = calc_fog_spread(temp_now, dp_val)
fog_spread = fog_spread if fog_spread is not None else 20
fog_spread = max(0, min(30, fog_spread))
fog_color  = "#FF3333" if fog_spread < 3 else "#FF8C00" if fog_spread < 9 else "#FFD700" if fog_spread < 18 else "#00FF9C"
fog_label  = "FOG IMMINENT" if fog_spread < 3 else "HIGH RISK" if fog_spread < 9 else "MODERATE" if fog_spread < 18 else "CLEAR"

# AQI
aqi_val     = aqi_data.get("aqi", 0) if aqi_data.get("ok") else 0
aqi_display = min(aqi_val, 200)
aqi_color   = "#00FF9C" if aqi_val <= 50 else "#AAFF00" if aqi_val <= 100 else "#FFD700" if aqi_val <= 150 else "#FF8C00" if aqi_val <= 200 else "#FF3333"
aqi_label   = "GOOD" if aqi_val <= 50 else "MODERATE" if aqi_val <= 100 else "SENSITIVE" if aqi_val <= 150 else "UNHEALTHY" if aqi_val <= 200 else "HAZARDOUS"

now = datetime.now(ZoneInfo("America/New_York"))

# ─────────────────────────────────────────────
#  RENDER
# ─────────────────────────────────────────────
st.markdown(f"""
<div class="site-header">
    <div class="site-title">CULLOWHEE WEATHER INTELLIGENCE</div>
    <div class="site-sub">North Carolina Center for the Advancement of Teaching — Cullowhee, NC &nbsp;|&nbsp;
    {now.strftime('%A, %B %d, %Y  %I:%M %p')} EST</div>
    <div style="margin-top:8px;">
        <span class="source-badge">📡 AWN: {'LIVE' if ambient.get('ok') else 'OFFLINE'}</span>
        <span class="source-badge">⚡ BLITZORTUNG: {'LIVE' if blitz.get('ok') else 'OFFLINE'}</span>
        <span class="source-badge">✈️ AIRPORT 24A: {'LIVE' if airport.get('ok') else 'OFFLINE'}</span>
        <span class="source-badge">💧 USGS: {'LIVE' if any(v['ok'] for v in usgs.values()) else 'OFFLINE'}</span>
        <span class="source-badge">🌬️ AQI: {'LIVE' if aqi_data.get('ok') else 'OFFLINE'}</span>
        <span class="source-badge">🌐 OPEN-METEO: LIVE</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ── GAUGE ROW 1 ──
st.markdown('<div class="panel"><div class="panel-title">⚡ Hazard & Atmospheric Gauges</div>', unsafe_allow_html=True)
r1c1, r1c2, r1c3, r1c4, r1c5 = st.columns(5)

with r1c1:
    fig = make_gauge(l_display, "LIGHTNING PROXIMITY", min_val=0, max_val=25, unit=" mi", color=l_color,
        thresholds=[{"range":[0,5],"color":"rgba(255,51,51,0.12)"},{"range":[5,10],"color":"rgba(255,140,0,0.12)"},
                    {"range":[10,15],"color":"rgba(255,215,0,0.12)"},{"range":[15,25],"color":"rgba(0,255,156,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(sublabel(l_label, l_color), unsafe_allow_html=True)
    st.markdown(subsub(f"Strikes Today: <b style='color:#00FFCC'>{l_strikes}</b>"), unsafe_allow_html=True)
    st.markdown(srctag(l_source), unsafe_allow_html=True)

with r1c2:
    fig = make_gauge(uv_val, "UV INDEX", min_val=0, max_val=12, unit="", color=uv_color,
        thresholds=[{"range":[0,3],"color":"rgba(0,255,156,0.12)"},{"range":[3,6],"color":"rgba(255,215,0,0.12)"},
                    {"range":[6,8],"color":"rgba(255,140,0,0.12)"},{"range":[8,12],"color":"rgba(255,51,51,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(sublabel(uv_label, uv_color), unsafe_allow_html=True)
    st.markdown(subsub("Protect skin &gt;3 | Seek shade &gt;6"), unsafe_allow_html=True)
    st.markdown(srctag("AWN SENSOR"), unsafe_allow_html=True)

with r1c3:
    fig = make_gauge(fl_display, "FEELS LIKE", min_val=0, max_val=120, unit="&deg;F", color=fl_color,
        thresholds=[{"range":[0,32],"color":"rgba(90,200,250,0.12)"},{"range":[32,60],"color":"rgba(0,255,255,0.12)"},
                    {"range":[60,85],"color":"rgba(0,255,156,0.12)"},{"range":[85,105],"color":"rgba(255,140,0,0.12)"},
                    {"range":[105,120],"color":"rgba(255,51,51,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(sublabel(fl_label, fl_color), unsafe_allow_html=True)
    actual_str = f"Actual: <b style='color:#00FFCC'>{temp_now}&deg;F</b>" if temp_now else "Actual: --"
    st.markdown(subsub(actual_str), unsafe_allow_html=True)
    st.markdown(srctag("AWN + CALC"), unsafe_allow_html=True)

with r1c4:
    fig = make_gauge(hum_val, "HUMIDITY", min_val=0, max_val=100, unit="%", color=hum_color,
        thresholds=[{"range":[0,30],"color":"rgba(90,200,250,0.12)"},{"range":[30,60],"color":"rgba(0,255,156,0.12)"},
                    {"range":[60,80],"color":"rgba(255,215,0,0.12)"},{"range":[80,100],"color":"rgba(255,140,0,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(sublabel(hum_label, hum_color), unsafe_allow_html=True)
    dp_str = f"Dewpoint: <b style='color:#00FFCC'>{dp_val}&deg;F</b>" if dp_val else "Dewpoint: --"
    st.markdown(subsub(dp_str), unsafe_allow_html=True)
    st.markdown(srctag("AWN SENSOR"), unsafe_allow_html=True)

with r1c5:
    w_color = "#00FF9C" if wind_now < 15 else "#FFD700" if wind_now < 25 else "#FF8C00" if wind_now < 35 else "#FF3333"
    w_label = "CALM" if wind_now < 15 else "BREEZY" if wind_now < 25 else "STRONG" if wind_now < 35 else "DANGEROUS"
    fig = make_gauge(wind_now, "WIND SPEED", min_val=0, max_val=60, unit=" mph", color=w_color,
        thresholds=[{"range":[0,15],"color":"rgba(0,255,156,0.12)"},{"range":[15,25],"color":"rgba(255,215,0,0.12)"},
                    {"range":[25,35],"color":"rgba(255,140,0,0.12)"},{"range":[35,60],"color":"rgba(255,51,51,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(sublabel(w_label, w_color), unsafe_allow_html=True)
    st.markdown(subsub(f"Gust: <b style='color:#00FFCC'>{ambient.get('wind_gust','--')} mph</b>"), unsafe_allow_html=True)
    st.markdown(srctag("AWN SENSOR"), unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# ── GAUGE ROW 2 ──
st.markdown('<div class="panel"><div class="panel-title">🌱 Site Condition Gauges</div>', unsafe_allow_html=True)
r2c1, r2c2, r2c3, r2c4, r2c5 = st.columns(5)

with r2c1:
    fig = make_gauge(soil_pct, "SOIL MOISTURE", color=soil_color,
        thresholds=[{"range":[0,25],"color":"rgba(90,200,250,0.12)"},{"range":[25,50],"color":"rgba(0,255,156,0.12)"},
                    {"range":[50,75],"color":"rgba(255,215,0,0.12)"},{"range":[75,100],"color":"rgba(255,51,51,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(sublabel(soil_status, soil_color), unsafe_allow_html=True)
    st.markdown(subsub(f"Storage: <b style='color:#00FFCC'>{soil_storage} in</b>"), unsafe_allow_html=True)
    st.markdown(srctag("WATER BALANCE MODEL"), unsafe_allow_html=True)

with r2c2:
    p_color = pop_color(pop_today)
    p_label = "DRY" if pop_today < 20 else "SLIGHT" if pop_today < 40 else "CHANCE" if pop_today < 60 else "LIKELY" if pop_today < 80 else "CERTAIN"
    fig = make_gauge(pop_today, "PRECIP PROBABILITY", unit="%", color=p_color,
        thresholds=[{"range":[0,20],"color":"rgba(0,255,156,0.12)"},{"range":[20,50],"color":"rgba(255,215,0,0.12)"},
                    {"range":[50,80],"color":"rgba(255,140,0,0.12)"},{"range":[80,100],"color":"rgba(255,51,51,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(sublabel(p_label, p_color), unsafe_allow_html=True)
    st.markdown(subsub(f"Rain Today: <b style='color:#00FFCC'>{rain_today}&quot;</b>"), unsafe_allow_html=True)
    st.markdown(srctag("OPEN-METEO HRRR/GFS"), unsafe_allow_html=True)

with r2c3:
    fig = make_gauge(freeze_display, "TONIGHT'S LOW / FREEZE RISK", min_val=0, max_val=80, unit="&deg;F", color=freeze_color,
        thresholds=[{"range":[0,28],"color":"rgba(255,51,51,0.12)"},{"range":[28,32],"color":"rgba(255,140,0,0.12)"},
                    {"range":[32,45],"color":"rgba(255,215,0,0.12)"},{"range":[45,80],"color":"rgba(0,255,156,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(sublabel(freeze_label, freeze_color), unsafe_allow_html=True)
    st.markdown(subsub(f"Tonight Low: <b style='color:#00FFCC'>{tonight_low}&deg;F</b>"), unsafe_allow_html=True)
    st.markdown(srctag("OPEN-METEO HRRR/GFS"), unsafe_allow_html=True)

with r2c4:
    fig = make_gauge(fog_spread, "FOG RISK (DEW SPREAD)", min_val=0, max_val=30, unit="&deg;F", color=fog_color,
        thresholds=[{"range":[0,3],"color":"rgba(255,51,51,0.12)"},{"range":[3,9],"color":"rgba(255,140,0,0.12)"},
                    {"range":[9,18],"color":"rgba(255,215,0,0.12)"},{"range":[18,30],"color":"rgba(0,255,156,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(sublabel(fog_label, fog_color), unsafe_allow_html=True)
    st.markdown(subsub("Lower spread = higher fog risk"), unsafe_allow_html=True)
    st.markdown(srctag("AWN + CALC"), unsafe_allow_html=True)

with r2c5:
    fig = make_gauge(aqi_display, "AIR QUALITY INDEX", min_val=0, max_val=200, unit="", color=aqi_color,
        thresholds=[{"range":[0,50],"color":"rgba(0,255,156,0.12)"},{"range":[50,100],"color":"rgba(255,215,0,0.12)"},
                    {"range":[100,150],"color":"rgba(255,140,0,0.12)"},{"range":[150,200],"color":"rgba(255,51,51,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(sublabel(aqi_label, aqi_color), unsafe_allow_html=True)
    aqi_display_val = aqi_val if aqi_data.get("ok") else "--"
    st.markdown(subsub(f"AQI: <b style='color:#00FFCC'>{aqi_display_val}</b> (US EPA Scale)"), unsafe_allow_html=True)
    st.markdown(srctag("OPEN-METEO AQ API"), unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# ── 7-DAY FORECAST ──
st.markdown('<div class="panel"><div class="panel-title">📅 7-Day Multi-Model Forecast</div>', unsafe_allow_html=True)
cols = st.columns(7)
for i, day in enumerate(forecast):
    pc = pop_color(day["pop"])
    with cols[i]:
        st.markdown(f"""
        <div style="background:rgba(0,136,255,0.05);border:1px solid rgba(0,136,255,0.2);
                    border-top:3px solid {pc};border-radius:8px;padding:10px 6px;text-align:center;">
            <div style="font-family:'Rajdhani';font-weight:700;color:#FFFFFF;font-size:1.1em;">{day['day']}</div>
            <div style="font-family:'Share Tech Mono';font-size:0.68em;color:#7AACCC;">{day['date'].split()[1]}</div>
            <div style="margin:6px 0;">
                <span style="color:#FF6B35;font-weight:700;font-size:1.2em;">{day['hi']}&deg;</span>
                <span style="color:#5AC8FA;font-size:0.95em;"> / {day['lo']}&deg;</span>
            </div>
            <div style="color:{pc};font-weight:700;font-size:0.95em;">{day['pop']}%</div>
            <div style="color:#00FFCC;font-size:0.82em;">{day['precip']}&quot;</div>
            <div style="font-size:0.65em;color:#7AACCC;margin-top:2px;">{day['desc']}</div>
            <div style="margin-top:6px;background:rgba(0,255,180,0.08);border:1px solid rgba(0,255,180,0.2);
                        border-radius:3px;padding:1px 4px;font-size:0.62em;color:#00FFB4;
                        font-family:'Share Tech Mono';">{day['model']}</div>
        </div>
        """, unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

# ── DATA SOURCE PANELS ──
live_panels = []
if ambient.get("ok"):
    live_panels.append("ambient")
if airport.get("ok"):
    live_panels.append("airport")
usgs_live = {k: v for k, v in usgs.items() if v["ok"]}
if usgs_live:
    live_panels.append("usgs")
live_panels.append("soil")

if live_panels:
    panel_cols = st.columns(len(live_panels))
    for idx, panel_key in enumerate(live_panels):
        with panel_cols[idx]:
            if panel_key == "ambient":
                st.markdown('<div class="panel"><div class="panel-title">📡 Riverbend on the Tuckasegee (AWN)</div>', unsafe_allow_html=True)
                st.caption(f"Station: {ambient['name']}")
                c1, c2 = st.columns(2)
                c1.metric("Temperature",  f"{ambient['temp']}°F")
                c2.metric("Humidity",     f"{ambient['humidity']}%")
                c1.metric("Rain Today",   f"{ambient['rain_today']}\"")
                c2.metric("Rain/Hour",    f"{ambient['rain_1hr']}\"")
                c1.metric("Rain 7-Day",   f"{ambient['rain_week']}\"")
                c2.metric("Rain 30-Day",  f"{ambient['rain_month']}\"")
                c1.metric("Pressure",     f"{ambient['pressure']} inHg")
                c2.metric("Solar Rad",    f"{ambient['solar']} W/m²")
                st.markdown('</div>', unsafe_allow_html=True)

            elif panel_key == "airport":
                st.markdown('<div class="panel"><div class="panel-title">✈️ Jackson County Airport (24A)</div>', unsafe_allow_html=True)
                c1, c2 = st.columns(2)
                c1.metric("Temperature",  f"{airport['temp_f']}°F" if airport.get('temp_f') else "--")
                c2.metric("Wind",         f"{airport['wind_mph']} mph" if airport.get('wind_mph') else "--")
                c1.metric("Wind Dir",     f"{airport['wind_dir']}°" if airport.get('wind_dir') else "--")
                c2.metric("Altimeter",    f"{airport['altim']} inHg" if airport.get('altim') else "--")
                c1.metric("Sky Cover",    airport.get("cover", "--"))
                c2.metric("Precip",       f"{airport.get('precip', 0.0)}\"")
                st.caption(f"Raw METAR: `{airport.get('raw','')}`")
                st.caption(f"Obs Time: {airport.get('time','')}")
                st.markdown('</div>', unsafe_allow_html=True)

            elif panel_key == "usgs":
                st.markdown('<div class="panel"><div class="panel-title">💧 USGS Stream Gauges</div>', unsafe_allow_html=True)
                for site_id, info in usgs_live.items():
                    st.metric(f"🟢 {info['name']}", f"{info['value']}\" precip")
                st.markdown('</div>', unsafe_allow_html=True)

            elif panel_key == "soil":
                st.markdown('<div class="panel"><div class="panel-title">🌱 Soil Moisture Estimate</div>', unsafe_allow_html=True)
                rain_30d_total = round(sum(hist_rain), 2)
                st.markdown(f"""
                <div style="font-family:'Share Tech Mono';font-size:0.8em;color:#7AACCC;line-height:1.8;">
                <b style="color:#FFFFFF">Model:</b> Water Balance Bucket<br>
                <b style="color:#FFFFFF">Soil Type:</b> Mountain Clay Loam (Ultisol)<br>
                <b style="color:#FFFFFF">Root Zone:</b> 12 inches<br>
                <b style="color:#FFFFFF">Field Capacity:</b> 2.16 in storage<br>
                <b style="color:#FFFFFF">Current Storage:</b> {soil_storage} in<br>
                <b style="color:#FFFFFF">Saturation:</b> <span style="color:{soil_color};font-weight:700;">{soil_pct}% &mdash; {soil_status}</span><br>
                <b style="color:#FFFFFF">30-Day Rain:</b> {rain_30d_total}&quot;
                </div>
                """, unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

# ── RADAR ──
st.markdown('<div class="panel"><div class="panel-title">🛰️ Live Radar — Jackson County / Cullowhee, NC</div>', unsafe_allow_html=True)
st.components.v1.html(
    '<iframe width="100%" height="500" src="https://embed.windy.com/embed2.html?lat=35.308&lon=-83.175&zoom=9&overlay=radar&product=radar&level=surface" frameborder="0" style="border-radius:8px;"></iframe>',
    height=510
)
st.markdown('</div>', unsafe_allow_html=True)

st.markdown(f"""
<div style="text-align:center;font-family:'Share Tech Mono';font-size:0.7em;color:#2A4060;margin-top:20px;">
CULLOWHEE WEATHER INTELLIGENCE &nbsp;|&nbsp; {SITE} &nbsp;|&nbsp;
Sources: Riverbend AWN &middot; Blitzortung &middot; NOAA/24A &middot; USGS 03439000/03460000 &middot; Open-Meteo (HRRR/GFS/AQ) &nbsp;|&nbsp; Auto-refresh: 5 min
</div>
""", unsafe_allow_html=True)
