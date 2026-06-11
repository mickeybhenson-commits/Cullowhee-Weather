"""
streamlit_app.py  —  NOAH · Cullowhee Creek Flood Warning System
=====================================================================
Operations-console presentation of the watershed flood model.
Demonstration build: synthetic scenario + provisional parameters.
=====================================================================
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from google.cloud import firestore

try:
    import flood_engine
    import flood_network
    import orographic
    FLOOD_OK = True
except Exception as _e:
    FLOOD_OK = False
    _FLOOD_ERR = str(_e)

PROJECT_ID = "ee-dashboard-477704"
DATABASE   = "cullowhee"
TIMEZONE   = ZoneInfo("America/New_York")
SENTINEL   = -1
LAT, LON = 35.307205, -83.182899
NWS_USER_AGENT = "(WCU-NOAH/1.0 mickey.b.henson@gmail.com)"

COORDS = {"belk": (35.3075, -83.1830), "double_springs": (35.2120, -83.1835),
          "aahp": (35.2530, -83.2340)}

SEV = {"NORMAL": "#1A7A52", "WATCH": "#C08A00", "WARNING": "#C2410C", "EMERGENCY": "#B42318"}
ORDER = ["NORMAL", "WATCH", "WARNING", "EMERGENCY"]
THRESH_FT = {"WATCH": 7, "WARNING": 9, "EMERGENCY": 11}
STATEMENT = {
    "NORMAL": "No flood threat indicated. Monitoring nominal.",
    "WATCH": "Conditions favorable for flooding. Upstream sub-basins primed — monitor closely.",
    "WARNING": "Flood warning conditions developing in upstream sub-basins.",
    "EMERGENCY": "Imminent flood threat indicated in the Cullowhee Creek corridor.",
}
STATUS_COLOR = {"live": "#1A7A52", "synthetic": "#C08A00",
                "placeholder": "#92633A", "modeled": "#1C6E8C", "none": "#8A97A4"}

def olp_category(idx):
    return ("negligible" if idx < 0.25 else "moderate" if idx < 0.5
            else "strong" if idx < 0.8 else "extreme")

st.set_page_config(page_title="NOAH · Cullowhee Flood Warning",
                   page_icon="🌊", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@600;700;800&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
#MainMenu, header[data-testid="stHeader"], footer {visibility:hidden; height:0;}
.stApp {background:#EEF1F4;}
.block-container {max-width:1180px; padding-top:0.5rem; padding-bottom:2rem;}
html, body, [class*="css"] {font-family:'Inter',sans-serif; color:#1B2A38;}
.mono {font-family:'IBM Plex Mono',monospace;}

.appbar {background:#13212E; color:#fff; border-radius:10px; padding:18px 24px;
         display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;}
.wordmark {font-family:'Archivo',sans-serif; font-weight:800; font-size:1.9rem;
           letter-spacing:1px; line-height:1;}
.brand-sub {font-size:0.92rem; color:#9DB2C4; margin-top:4px;}
.appbar-right {text-align:right;}
.mode-badge {display:inline-block; background:#C08A00; color:#1B2A38; font-weight:600;
             font-size:0.72rem; letter-spacing:0.5px; padding:3px 10px; border-radius:4px;
             text-transform:uppercase;}
.appbar-meta {font-size:0.76rem; color:#9DB2C4; margin-top:6px;}

.threat {border:1px solid; border-left-width:8px; border-radius:8px; padding:16px 22px;
         margin-bottom:14px; background:#fff;}
.threat-level {font-family:'Archivo',sans-serif; font-weight:800; font-size:1.7rem;
               letter-spacing:1px;}
.threat-statement {font-size:1.0rem; color:#2C3E50; margin:2px 0 10px;}
.threat-metrics {display:flex; gap:28px; flex-wrap:wrap; font-size:0.9rem; color:#4A5A6A;}
.threat-metrics b {font-family:'IBM Plex Mono',monospace; color:#1B2A38; font-size:1.05rem;}
.threat-note {font-size:0.82rem; color:#6B7C8C; margin-top:8px; font-style:italic;}

.eyebrow {font-family:'Archivo',sans-serif; font-weight:700; font-size:0.78rem;
          letter-spacing:1.5px; text-transform:uppercase; color:#5B6B7A;
          margin:18px 0 8px; border-bottom:1px solid #D6DDE3; padding-bottom:6px;}

.legend {display:flex; gap:10px; flex-wrap:wrap; margin-bottom:6px;}
.legend-chip {display:flex; align-items:center; gap:7px; background:#fff; border:1px solid #DCE2E8;
              border-radius:6px; padding:5px 11px; font-size:0.82rem;}
.legend-dot {width:11px; height:11px; border-radius:2px;}
.legend-th {font-family:'IBM Plex Mono',monospace; color:#6B7C8C; font-size:0.76rem;}

.grid {display:grid; gap:12px;}
.sites {grid-template-columns:repeat(auto-fit,minmax(220px,1fr));}
.fcast {grid-template-columns:repeat(7,minmax(0,1fr));}

.card {background:#fff; border:1px solid #DCE2E8; border-radius:8px; padding:14px 16px;}
.site-name {font-family:'Archivo',sans-serif; font-weight:700; font-size:1.05rem;}
.site-role {font-size:0.68rem; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;
            color:#7A8896; background:#EEF1F4; padding:1px 6px; border-radius:3px; margin-left:6px;}
.site-coord {font-family:'IBM Plex Mono',monospace; font-size:0.72rem; color:#8A97A4; margin-top:3px;}
.site-level {font-family:'Archivo',sans-serif; font-weight:800; font-size:1.15rem; margin:8px 0 2px;}
.site-detail {font-size:0.8rem; color:#5B6B7A; margin-top:3px; line-height:1.4;}

.ftile {background:#fff; border:1px solid #DCE2E8; border-radius:8px; padding:10px 6px; text-align:center;}
.ftile-today {border:2px solid #1C6E8C;}
.ftile-day {font-family:'IBM Plex Mono',monospace; font-size:0.72rem; color:#6B7C8C;}
.ftile-hi {font-weight:700; font-size:1.05rem;}
.ftile-lo {font-size:0.8rem; color:#8A97A4;}
.ftile-desc {font-size:0.66rem; color:#7A8896; margin:3px 0;}
.ftile-src {font-family:'IBM Plex Mono',monospace; font-size:0.62rem; font-weight:600;}

.chiprow {display:flex; gap:7px; flex-wrap:wrap; align-items:center; margin:5px 0;}
.chip {border-radius:10px; padding:1px 9px; font-size:0.76rem; border:1px solid;}
.chip-label {font-weight:600; min-width:120px; display:inline-block;}

.footer {background:#13212E; color:#B7C6D4; border-radius:10px; padding:18px 24px;
         margin-top:22px; font-size:0.82rem; line-height:1.6;}
.footer b {color:#fff;}
.disclaimer {color:#E6B85C; margin-top:10px; font-size:0.8rem;}
</style>
""", unsafe_allow_html=True)

@st.cache_resource(show_spinner=False)
def get_db():
    if "gcp_service_account" in st.secrets:
        import json
        from google.oauth2 import service_account
        sa = st.secrets["gcp_service_account"]
        info = json.loads(sa["json"]) if "json" in sa else dict(sa)
        creds = service_account.Credentials.from_service_account_info(info)
        return firestore.Client(project=PROJECT_ID, database=DATABASE, credentials=creds)
    return firestore.Client(project=PROJECT_ID, database=DATABASE)

_DT_RE = re.compile(r"DatetimeWithNanoseconds\(\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),"
                    r"\s*(\d+),\s*(\d+)(?:,\s*(\d+))?")

def parse_time(raw, doc):
    dt = None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        m = _DT_RE.search(raw)
        if m:
            parts = [int(x) for x in m.groups() if x is not None]
            try: dt = datetime(*parts, tzinfo=timezone.utc)
            except (ValueError, TypeError): dt = None
        if dt is None:
            try: dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            except ValueError: dt = None
    if dt is None:
        ct = getattr(doc, "create_time", None)
        if ct is not None: dt = ct
    if dt is None: return None
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TIMEZONE)

def _clean(v):
    try: v = float(v)
    except (TypeError, ValueError): return None
    return None if v == SENTINEL else v

def weather_desc(code):
    c = {0:"Clear",1:"Mainly Clear",2:"Partly Cloudy",3:"Overcast",45:"Foggy",48:"Rime Fog",
         51:"Lt Drizzle",53:"Drizzle",55:"Hvy Drizzle",61:"Lt Rain",63:"Rain",65:"Hvy Rain",
         71:"Lt Snow",73:"Snow",75:"Hvy Snow",80:"Showers",81:"Mod Showers",82:"Hvy Showers",
         95:"Tstorm",96:"Tstm+Hail",99:"Tstm+Hail"}
    return c.get(code, "—")

def weather_emoji(code):
    if code in (95,96,99): return "⛈"
    if code in (80,81,82,61,63,65,51,53,55): return "🌧"
    if code in (71,73,75): return "❄"
    if code in (45,48): return "🌫"
    if code == 3: return "☁"
    if code == 2: return "⛅"
    if code in (0,1): return "☀"
    return "🌤"

def nws_desc_to_code(desc):
    d = desc.lower()
    if any(x in d for x in ["thunderstorm","tstm","lightning"]): return 95
    if any(x in d for x in ["blizzard","snow","sleet","freezing","wintry"]): return 73
    if "shower" in d: return 80
    if "rain" in d: return 63
    if "drizzle" in d: return 51
    if any(x in d for x in ["fog","mist"]): return 45
    if "overcast" in d or "cloudy" in d: return 3
    if "partly" in d: return 2
    if any(x in d for x in ["sunny","clear","fair"]): return 0
    return 1

def pop_color(pop):
    return ("#1A7A52" if pop < 20 else "#7FA31E" if pop < 40 else
            "#C08A00" if pop < 60 else "#C2410C" if pop < 80 else "#B42318")

@st.cache_data(ttl=900, show_spinner=False)
def fetch_nws_forecast():
    try:
        pts = requests.get(f"https://api.weather.gov/points/{LAT},{LON}",
                           headers={"User-Agent": NWS_USER_AGENT}, timeout=10); pts.raise_for_status()
        fc = requests.get(pts.json()["properties"]["forecast"],
                          headers={"User-Agent": NWS_USER_AGENT}, timeout=10); fc.raise_for_status()
        byday = {}
        for p in fc.json()["properties"]["periods"]:
            d = datetime.fromisoformat(p["startTime"]).date()
            byday.setdefault(d, {})["day" if p["isDaytime"] else "night"] = p
        days = []
        for d in sorted(byday)[:7]:
            dp = byday[d]; day_p = dp.get("day"); night_p = dp.get("night")
            primary = day_p or night_p
            if not primary: continue
            dpop = (day_p.get("probabilityOfPrecipitation") or {}).get("value") or 0 if day_p else 0
            npop = (night_p.get("probabilityOfPrecipitation") or {}).get("value") or 0 if night_p else 0
            days.append({"date": d.strftime("%Y-%m-%d"),
                         "label": datetime.combine(d, datetime.min.time()).strftime("%a %m/%d"),
                         "hi": day_p["temperature"] if day_p else None,
                         "lo": night_p["temperature"] if night_p else None,
                         "pop": max(dpop, npop), "code": nws_desc_to_code(primary["shortForecast"]),
                         "desc": primary["shortForecast"][:16], "source": "NWS"})
        return {"days": days, "error": None}
    except Exception as e:
        return {"days": [], "error": str(e)}

def _om(url, model=None, ndays=7):
    params = {"latitude": LAT, "longitude": LON,
              "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                       "precipitation_probability_max", "temperature_unit": "fahrenheit",
              "precipitation_unit": "inch", "timezone": "America/New_York", "forecast_days": ndays}
    if model: params["models"] = model
    r = requests.get(url, params=params, timeout=12); r.raise_for_status()
    d = r.json().get("daily", {}); days = []
    for i in range(len(d.get("time", []))):
        dt = datetime.strptime(d["time"][i], "%Y-%m-%d"); code = d["weathercode"][i] or 0
        days.append({"date": d["time"][i], "label": dt.strftime("%a %m/%d"),
                     "hi": round(d["temperature_2m_max"][i]) if d["temperature_2m_max"][i] is not None else None,
                     "lo": round(d["temperature_2m_min"][i]) if d["temperature_2m_min"][i] is not None else None,
                     "pop": int(round(d["precipitation_probability_max"][i] or 0)),
                     "code": code, "desc": weather_desc(code)})
    return days

@st.cache_data(ttl=900, show_spinner=False)
def fetch_hrrr():
    try:
        days = _om("https://api.open-meteo.com/v1/gfs", "hrrr_conus", 3)
        for x in days: x["source"] = "HRRR"
        return {"days": days, "error": None}
    except Exception as e: return {"days": [], "error": str(e)}

@st.cache_data(ttl=900, show_spinner=False)
def fetch_ecmwf():
    try:
        days = _om("https://api.open-meteo.com/v1/ecmwf", None, 7)
        for x in days: x["source"] = "ECMWF"
        return {"days": days, "error": None}
    except Exception as e: return {"days": [], "error": str(e)}

@st.cache_data(ttl=900, show_spinner="Building forecast…")
def fetch_best_7day():
    nws = fetch_nws_forecast(); hrrr = fetch_hrrr(); ecmwf = fetch_ecmwf()
    nd = {x["date"]: x for x in nws["days"]}; hd = {x["date"]: x for x in hrrr["days"]}
    ed = {x["date"]: x for x in ecmwf["days"]}; today = datetime.now(TIMEZONE).date(); out = []
    for i in range(7):
        key = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        h, n, e = hd.get(key), nd.get(key), ed.get(key)
        if i <= 1 and h:
            day = dict(h);  day["pop"] = max(h["pop"], n.get("pop", 0)) if n else h["pop"]
        elif i >= 4 and e:
            day = dict(e);  day["pop"] = max(e["pop"], n.get("pop", 0)) if n else e["pop"]
        elif n: day = dict(n)
        elif e: day = dict(e)
        elif h: day = dict(h)
        else: day = {"date": key, "hi": None, "lo": None, "pop": 0, "code": 0, "desc": "—", "source": "N/A"}
        day["label"] = datetime.strptime(key, "%Y-%m-%d").strftime("%a %m/%d"); out.append(day)
    errs = {k: v["error"] for k, v in [("NWS", nws), ("HRRR", hrrr), ("ECMWF", ecmwf)] if v["error"]}
    return {"days": out, "errors": errs}

SRC_COLOR = {"HRRR": "#0F6E56", "NWS": "#1C6E8C", "ECMWF": "#534AB7", "N/A": "#8A97A4"}

@st.cache_data(ttl=300, show_spinner=False)
def fetch_stage_series(collection, max_docs=2000):
    if not collection: return None
    try:
        db = get_db(); docs = list(db.collection(collection).limit(max_docs).stream())
    except Exception: return None
    rows = []
    for d in docs:
        rec = d.to_dict() or {}; t = parse_time(rec.get("timestamp"), d); s = _clean(rec.get("stage_ft"))
        if t is None or s is None: continue
        rows.append((int(t.timestamp()), s))
    rows.sort(); return rows or None

def assemble_live_inputs():
    inputs = {}
    if not FLOOD_OK: return inputs
    for sid, s in flood_network.SITES.items():
        series = fetch_stage_series(s.get("stage_coll"))
        if series: inputs[sid] = {"stage_series": series}
    return inputs

def demo_inputs():
    now = datetime.now(TIMEZONE); step, hours = 5, 3; n = (hours*60)//step
    base, peak = 4.0, 9.5; rising = []
    for k in range(n):
        t = now - timedelta(minutes=(n-k)*step); frac = (k+1)/n
        rising.append((int(t.timestamp()), round(base + (peak-base)*(frac**1.6), 3)))
    return {"double_springs": {"stage_series": rising, "storm_rain_in": 1.0, "soil_pct": 88.0},
            "aahp": {"storm_rain_in": 1.5, "soil_pct": 88.0}}

def demo_orographic():
    a = orographic.lift_potential("aahp", 66, 95, 26.6, 25, 135)
    d = orographic.lift_potential("double_springs", 63, 88, 27.0, 15, 130)
    out = {}
    if a: out["aahp"] = a["olp_index"]
    if d: out["double_springs"] = d["olp_index"]
    return out

def overall_level(rw):
    lv = []
    if rw.local: lv.append(rw.local.level)
    for c in rw.upstream:
        if c.level: lv.append(c.level)
    return max(lv, key=lambda L: ORDER.index(L)) if lv else "NORMAL"

now_str = datetime.now(TIMEZONE).strftime("%a %b %d, %Y · %I:%M %p %Z")
st.markdown(f"""
<div class="appbar">
  <div>
    <div class="wordmark">NOAH</div>
    <div class="brand-sub">Cullowhee Creek Flood Warning System</div>
  </div>
  <div class="appbar-right">
    <span class="mode-badge">Demonstration · synthetic data</span>
    <div class="appbar-meta">Western Carolina University · College of Engineering</div>
    <div class="appbar-meta mono">Generated {now_str}</div>
  </div>
</div>
""", unsafe_allow_html=True)

if not FLOOD_OK:
    st.error(f"Flood modules could not be imported: {_FLOOD_ERR}")
    st.stop()

flood_network.recompute_travel_times()
live = assemble_live_inputs()
demo = st.toggle("Scenario: demonstration (synthetic)", value=not any(live.values()))
inputs = demo_inputs() if demo else live
oro = demo_orographic() if demo else {}
rw = flood_network.routed_assessment("belk", inputs, orographic_by_site=oro)
lvl = overall_level(rw)
col = SEV[lvl]

wp_name = flood_network.SITES["belk"]["name"]
lead = f"{round(rw.lead_time_hr * 60)} min" if rw.lead_time_hr is not None
