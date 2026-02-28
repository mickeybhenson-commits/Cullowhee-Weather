Here is the full code:

```python
import streamlit as st
import requests
import json
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta
from streamlit_autorefresh import st_autorefresh

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="NCCAT Weather Intelligence",
    layout="wide",
    page_icon="🏔️"
)
st_autorefresh(interval=300000, key="refresh")

LAT = 35.3079
LON = -83.1746
SITE = "NCCAT — Cullowhee, NC"

AMBIENT_API_KEY = st.secrets.get("AMBIENT_API_KEY", "9ed066cb260c42adbe8778e0afb09e747f8450a7dd20479791a18d692b722334")
AMBIENT_APP_KEY = st.secrets.get("AMBIENT_APP_KEY", "9ed066cb260c42adbe8778e0afb09e747f8450a7dd20479791a18d692b722334")

# ─────────────────────────────────────────────
#  STYLING
# ─────────────────────────────────────────────
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
            last = devices[0].get("lastData", {})
            return {
                "temp":         last.get("tempf"),
                "humidity":     last.get("humidity"),
                "wind_speed":   last.get("windspeedmph", 0),
                "wind_dir":     last.get("winddir", 0),
                "wind_gust":    last.get("windgustmph", 0),
                "rain_today":   last.get("dailyrainin", 0.0),
                "rain_1hr":     last.get("hourlyrainin", 0.0),
                "rain_week":    last.get("weeklyrainin", 0.0),
                "rain_month":   last.get("monthlyrainin", 0.0),
                "pressure":     last.get("baromrelin"),
                "uv":           last.get("uv", 0),
                "solar":        last.get("solarradiation", 0),
                "name":         devices[0].get("info", {}).get("name", "Local AWN Station"),
                "ok": True
            }
    except:
        pass
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
    model_params = {
        "hrrr":  "hrrr_conus",
        "ecmwf": "ecmwf_ifs04",
        "gfs":   "gfs_seamless",
    }
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
        if i <= 1:
            primary, secondary = "hrrr", "gfs"
        elif i <= 4:
            primary, secondary = "ecmwf", "gfs"
        else:
            primary, secondary = "gfs", "ecmwf"

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
            days.append({
                "date": date.strftime("%a %m/%d"), "day": date.strftime("%a"),
                "hi": 60, "lo": 40, "precip": 0.0, "pop": 10, "wind": 10,
                "code": 0, "model": "N/A", "desc": "Unknown"
            })
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

# ─────────────────────────────────────────────
#  SOIL MOISTURE MODEL
# ─────────────────────────────────────────────
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

# ─────────────────────────────────────────────
#  GAUGE CHART
# ─────────────────────────────────────────────
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
        number={"suffix": unit, "font": {"size": 28, "color": "#FFFFFF", "family": "Rajdhani"}},
        title={"text": title, "font": {"size": 13, "color": "#7AACCC", "family": "Share Tech Mono"}},
        gauge={
            "axis": {"range": [min_val, max_val], "tickwidth": 1, "tickcolor": "#2A4060",
                     "tickfont": {"color": "#5A7A9A", "size": 9}},
            "bar": {"color": color, "thickness": 0.25},
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 0,
            "steps": thresholds,
            "threshold": {"line": {"color": color, "width": 3}, "thickness": 0.85, "value": value}
        }
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=40, b=10, l=20, r=20), height=200,
        font={"color": "#E0E8F0"}
    )
    return fig

# ─────────────────────────────────────────────
#  COMPOSITE RISK SCORE
# ─────────────────────────────────────────────
def compute_risk(soil_pct, rain_today, rain_forecast_3d, wind_speed, pop_today):
    score = 0
    score += min(40, soil_pct * 0.4)
    score += min(20, rain_today * 40)
    score += min(20, rain_forecast_3d * 15)
    score += min(10, wind_speed * 0.4)
    score += min(10, pop_today * 0.1)
    score = min(100, score)
    if score < 25:   label, color = "LOW",      "#00FF9C"
    elif score < 50: label, color = "MODERATE", "#FFD700"
    elif score < 75: label, color = "HIGH",     "#FF8C00"
    else:            label, color = "CRITICAL", "#FF3333"
    return round(score, 1), label, color

# ─────────────────────────────────────────────
#  FETCH ALL DATA
# ─────────────────────────────────────────────
with st.spinner("Syncing all data sources..."):
    ambient   = fetch_ambient()
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
risk_score, risk_label, risk_color = compute_risk(soil_pct, rain_today, rain_3d_forecast, wind_now, pop_today)

now = datetime.now()

# ─────────────────────────────────────────────
#  RENDER
# ─────────────────────────────────────────────
st.markdown(f"""
<div class="site-header">
    <div class="site-title">🏔️ NCCAT WEATHER INTELLIGENCE</div>
    <div class="site-sub">National Center for the Advancement of Teaching — Cullowhee, NC &nbsp;|&nbsp;
    {now.strftime('%A, %B %d, %Y  %I:%M %p')}</div>
    <div style="margin-top:8px;">
        <span class="source-badge">📡 AWN: {'LIVE' if ambient.get('ok') else 'OFFLINE'}</span>
        <span class="source-badge">✈️ AIRPORT 24A: {'LIVE' if airport.get('ok') else 'OFFLINE'}</span>
        <span class="source-badge">💧 USGS: {'LIVE' if any(v['ok'] for v in usgs.values()) else 'OFFLINE'}</span>
        <span class="source-badge">🌐 OPEN-METEO: LIVE</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ── GAUGES ──
st.markdown('<div class="panel"><div class="panel-title">⚡ Site Condition Gauges</div>', unsafe_allow_html=True)
g1, g2, g3, g4 = st.columns(4)

with g1:
    fig = make_gauge(risk_score, "OVERALL SITE RISK", color=risk_color,
        thresholds=[{"range":[0,25],"color":"rgba(0,255,156,0.12)"},{"range":[25,50],"color":"rgba(255,215,0,0.12)"},
                    {"range":[50,75],"color":"rgba(255,140,0,0.12)"},{"range":[75,100],"color":"rgba(255,51,51,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(f"<div style='text-align:center;font-family:Rajdhani;font-size:1.4em;font-weight:700;color:{risk_color};'>{risk_label}</div>", unsafe_allow_html=True)

with g2:
    fig = make_gauge(soil_pct, "SOIL MOISTURE SATURATION", color=soil_color,
        thresholds=[{"range":[0,25],"color":"rgba(90,200,250,0.12)"},{"range":[25,50],"color":"rgba(0,255,156,0.12)"},
                    {"range":[50,75],"color":"rgba(255,215,0,0.12)"},{"range":[75,100],"color":"rgba(255,51,51,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(f"<div style='text-align:center;font-family:Rajdhani;font-size:1.4em;font-weight:700;color:{soil_color};'>{soil_status}</div>", unsafe_allow_html=True)

with g3:
    p_color = pop_color(pop_today)
    fig = make_gauge(pop_today, "PRECIP PROBABILITY", unit="%", color=p_color,
        thresholds=[{"range":[0,20],"color":"rgba(0,255,156,0.12)"},{"range":[20,50],"color":"rgba(255,215,0,0.12)"},
                    {"range":[50,80],"color":"rgba(255,140,0,0.12)"},{"range":[80,100],"color":"rgba(255,51,51,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(f"<div style='text-align:center;font-family:Rajdhani;font-size:1.4em;color:#7AACCC;'>Today's Rain: <b style='color:#00FFCC'>{rain_today}\"</b></div>", unsafe_allow_html=True)

with g4:
    w_color = "#00FF9C" if wind_now < 15 else "#FFD700" if wind_now < 25 else "#FF8C00" if wind_now < 35 else "#FF3333"
    fig = make_gauge(wind_now, "WIND SPEED", min_val=0, max_val=60, unit=" mph", color=w_color,
        thresholds=[{"range":[0,15],"color":"rgba(0,255,156,0.12)"},{"range":[15,25],"color":"rgba(255,215,0,0.12)"},
                    {"range":[25,35],"color":"rgba(255,140,0,0.12)"},{"range":[35,60],"color":"rgba(255,51,51,0.12)"}])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(f"<div style='text-align:center;font-family:Rajdhani;font-size:1.4em;color:#7AACCC;'>Gust: <b style='color:#00FFCC'>{ambient.get('wind_gust','--')} mph</b></div>", unsafe_allow_html=True)

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
                <span style="color:#FF6B35;font-weight:700;font-size:1.2em;">{day['hi']}°</span>
                <span style="color:#5AC8FA;font-size:0.95em;"> / {day['lo']}°</span>
            </div>
            <div style="color:{pc};font-weight:700;font-size:0.95em;">{day['pop']}%</div>
            <div style="color:#00FFCC;font-size:0.82em;">{day['precip']}"</div>
            <div style="font-size:0.65em;color:#7AACCC;margin-top:2px;">{day['desc']}</div>
            <div style="margin-top:6px;background:rgba(0,255,180,0.08);border:1px solid rgba(0,255,180,0.2);
                        border-radius:3px;padding:1px 4px;font-size:0.62em;color:#00FFB4;
                        font-family:'Share Tech Mono';">{day['model']}</div>
        </div>
        """, unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

# ── DATA SOURCES (only show live panels) ──
live_panels = []
if ambient.get("ok"):
    live_panels.append("ambient")
if airport.get("ok"):
    live_panels.append("airport")
usgs_live = {k: v for k, v in usgs.items() if v["ok"]}
if usgs_live:
    live_panels.append("usgs")
live_panels.append("soil")  # always show soil model

if live_panels:
    panel_cols = st.columns(len(live_panels))
    for idx, panel_key in enumerate(live_panels):
        with panel_cols[idx]:
            if panel_key == "ambient":
                st.markdown('<div class="panel"><div class="panel-title">📡 Ambient Weather Network</div>', unsafe_allow_html=True)
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
                st.markdown(f"""
                <div style="font-family:'Share Tech Mono';font-size:0.8em;color:#7AACCC;line-height:1.8;">
                <b style="color:#FFFFFF">Model:</b> Water Balance Bucket<br>
                <b style="color:#FFFFFF">Soil Type:</b> Mountain Clay Loam (Ultisol)<br>
                <b style="color:#FFFFFF">Root Zone:</b> 12 inches<br>
                <b style="color:#FFFFFF">Field Capacity:</b> 2.16 in storage<br>
                <b style="color:#FFFFFF">Current Storage:</b> {soil_storage} in<br>
                <b style="color:#FFFFFF">Saturation:</b> <span style="color:{soil_color};font-weight:700;">{soil_pct}% — {soil_status}</span><br>
                <b style="color:#FFFFFF">30-Day Rain:</b> {round(sum(hist_rain),2)}"
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

# Footer
st.markdown(f"""
<div style="text-align:center;font-family:'Share Tech Mono';font-size:0.7em;color:#2A4060;margin-top:20px;">
NCCAT WEATHER INTELLIGENCE &nbsp;|&nbsp; {SITE} &nbsp;|&nbsp;
Sources: AWN · NOAA/24A · USGS 03439000/03460000 · Open-Meteo (HRRR/ECMWF/GFS) &nbsp;|&nbsp; Auto-refresh: 5 min
</div>
""", unsafe_allow_html=True)
```
