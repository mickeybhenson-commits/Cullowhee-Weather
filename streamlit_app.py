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
import streamlit.components.v1 as components
import pydeck as pdk
from google.cloud import firestore

try:
    import flood_engine
    import flood_network
    import orographic
    import flood_profile
    FLOOD_OK = True
except Exception as _e:
    FLOOD_OK = False
    _FLOOD_ERR = str(_e)

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
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

# ---------------------------------------------------------------------
# STYLE
# ---------------------------------------------------------------------
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

.tiers {display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:12px 0 4px;}
.tier {background:#F7F9FB; border:1px solid #E2E8ED; border-left-width:5px; border-radius:7px; padding:10px 13px;}
.tier-head {font-family:'Archivo',sans-serif; font-weight:700; font-size:0.7rem; letter-spacing:1px;
            text-transform:uppercase; color:#6B7C8C;}
.tier-cap {font-size:0.62rem; color:#92A0AE; font-weight:600; letter-spacing:0.3px;}
.tier-level {font-family:'Archivo',sans-serif; font-weight:800; font-size:1.25rem; margin:3px 0;}
.tier-detail {font-size:0.78rem; color:#5B6B7A; line-height:1.4;}
@media (max-width:680px){.tiers{grid-template-columns:1fr;}}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------
# CONNECT / PARSE
# ---------------------------------------------------------------------
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

# ---------------------------------------------------------------------
# FORECAST
# ---------------------------------------------------------------------
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

# ---------------------------------------------------------------------
# STAGE + INPUTS
# ---------------------------------------------------------------------
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

# =====================================================================
# RENDER
# =====================================================================
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
tp = flood_network.tiered_posture(rw)
lvl = tp.headline
col = SEV[lvl]

# threat status — two tiers: Outlook (forecast/soil) vs Confirmation (measured stream)
wp_name = flood_network.SITES["belk"]["name"]
lead = f"{round(rw.lead_time_hr * 60)} min" if rw.lead_time_hr is not None else "—"
oc = SEV["WATCH"] if tp.outlook_level == "WATCH" else "#9AA8B5"
scol = SEV[tp.stream_level] if tp.stream_level != "NORMAL" else "#9AA8B5"
st.markdown(f"""
<div class="threat" style="border-color:{col};">
  <div class="threat-level" style="color:{col};">{lvl}</div>
  <div class="threat-statement">{tp.headline_statement}</div>
  <div class="tiers">
    <div class="tier" style="border-left-color:{oc};">
      <div class="tier-head">Outlook <span class="tier-cap">· soil + forecast · max WATCH</span></div>
      <div class="tier-level" style="color:{oc};">{tp.outlook_level}</div>
      <div class="tier-detail">Relative risk index <b>{tp.outlook_risk:.0%}</b> (uncalibrated). {tp.outlook_note}</div>
    </div>
    <div class="tier" style="border-left-color:{scol};">
      <div class="tier-head">Confirmation <span class="tier-cap">· measured stream · drives WARNING/EMERGENCY</span></div>
      <div class="tier-level" style="color:{scol};">{tp.stream_level}</div>
      <div class="tier-detail">{tp.stream_note}</div>
    </div>
  </div>
  <div class="threat-metrics">
    <span>Lead time to WCU Campus <b>{lead}</b></span>
    <span>Warning point <b class="mono">{wp_name}</b></span>
  </div>
  <div class="threat-note">Watch can be raised by soil moisture + forecast rain (lead-time signal).
  Warning and Emergency require a measured headwater stream rise (confirmation). The gauged
  downstream mainstem is a validation reference, not an input to either tier.</div>
</div>
""", unsafe_allow_html=True)

# legend
chips = "".join(
    f'<div class="legend-chip"><span class="legend-dot" style="background:{SEV[L]}"></span>'
    f'<span>{L.title()}</span>'
    f'<span class="legend-th">{("≥ "+str(THRESH_FT[L])+" ft") if L in THRESH_FT else "baseflow"}</span></div>'
    for L in ORDER)
st.markdown(f'<div class="legend">{chips}</div>', unsafe_allow_html=True)

# monitoring sites
st.markdown('<div class="eyebrow">Monitoring sites — upstream contributions routed to WCU Campus</div>',
            unsafe_allow_html=True)

def site_card(sid, contribution=None):
    name = flood_network.SITES[sid]["name"]
    elev = flood_network.SITES[sid].get("elevation_ft")
    _rolemap = {"warning": "outlet · warning point", "confluence": "confluence",
                "upstream": "upstream", "downstream": "downstream"}
    role = _rolemap.get(flood_network.SITES[sid].get("role"), "site")
    lat, lon = COORDS.get(sid, (None, None))
    coord = (f'{abs(lat):.4f}°N {abs(lon):.4f}°W · {elev:,.0f} ft'
             if lat is not None else f'{elev:,.0f} ft')
    if sid == "belk":
        if rw.local:
            c = SEV.get(rw.local.level, "#888")
            body = (f'<div class="site-level" style="color:{c};">{rw.local.level}</div>'
                    f'<div class="site-detail">Stage {rw.local.stage_ft} ft · '
                    f'discharge {rw.local.discharge_cfs:,.0f} cfs</div>')
        else:
            body = ('<div class="site-level" style="color:#8A97A4;">NO GAUGE</div>'
                    '<div class="site-detail">Stage node pending — warning relies on upstream sites.</div>')
    else:
        c = contribution
        if c.level is not None:
            lc = SEV.get(c.level, "#888")
            head = f'<div class="site-level" style="color:{lc};">{c.level}</div>'
        elif c.priming is not None:
            head = f'<div class="site-level" style="color:#C08A00;">PRIMED {c.priming:.0%}</div>'
        else:
            head = '<div class="site-level" style="color:#8A97A4;">STANDBY</div>'
        det = []
        sinp = inputs.get(sid, {})
        if sinp.get("storm_rain_in") is not None:
            b = flood_network.lead_time_breakdown(sid, sinp["storm_rain_in"], sinp.get("soil_pct"))
            det.append(f'Rain {sinp["storm_rain_in"]}″ → runoff {b["runoff_in"]}″ · '
                       f'campus rise ~{round(b["total_lead_hr"]*60)} min')
        else:
            det.append(f'~{round(c.eta_hr*60)} min channel travel to campus')
        if c.olp_index is not None:
            det.append(f'Orographic lift: {olp_category(c.olp_index)} ({c.olp_index:.0%}) · pre-rain')
        body = head + "".join(f'<div class="site-detail">{x}</div>' for x in det)
    border = SEV.get(rw.local.level, "#CBD3DA") if (sid == "belk" and rw.local) else "#CBD3DA"
    return (f'<div class="card" style="border-left:4px solid {border};">'
            f'<div class="site-name">{name}<span class="site-role">{role}</span></div>'
            f'<div class="site-coord mono">{coord}</div>{body}</div>')

cards = [site_card("belk")] + [site_card(c.site_id, c) for c in rw.upstream]
st.markdown(f'<div class="grid sites">{"".join(cards)}</div>', unsafe_allow_html=True)
st.markdown('<div class="site-detail" style="margin-top:8px;color:#8A97A4;">'
            'Body Farm is excluded — it enters the channel below the WCU Campus reach '
            'and cannot affect the warning point.'
            '</div>', unsafe_allow_html=True)

# incoming weather
# corridor profile (simulated depth & discharge along the creek)
# =========================================================================
# CULLOWHEE CREEK CORRIDOR — one combined, professional watershed map
# =========================================================================
st.markdown('<div class="eyebrow">Cullowhee Creek corridor \u2014 watershed map</div>',
            unsafe_allow_html=True)
with st.container(border=True):
    st.markdown("""
    <div style="display:flex;justify-content:space-between;align-items:center;margin:2px 2px 10px;">
      <div style="font-family:'Archivo',sans-serif;font-weight:700;font-size:1.08rem;color:#13212E;">
        Watershed corridor &middot; Watch / Warning / Emergency</div>
      <span style="font-size:0.66rem;font-weight:600;color:#92633A;background:#F3E9DC;
        padding:3px 10px;border-radius:4px;text-transform:uppercase;letter-spacing:0.5px;">Demonstration</span>
    </div>""", unsafe_allow_html=True)
    try:
        _ESRI_TOPO = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
                      "World_Topo_Map/MapServer/tile/{z}/{y}/{x}")
        _basin = flood_profile.BASIN_FEATURE
        _nodes = flood_profile.map_nodes()
        _reaches = flood_profile.map_reaches()
        _rlabels = flood_profile.map_reach_labels()
        _layers = [
            # professional topographic basemap (Esri/USGS) — real streams + hillshade
            pdk.Layer("TileLayer", data=_ESRI_TOPO, min_zoom=0, max_zoom=19, tile_size=256),
            # surveyed watershed boundary — crisp outline, faint fill
            pdk.Layer("PolygonLayer", _basin, get_polygon="polygon",
                      get_fill_color=[35, 79, 134, 12], get_line_color=[35, 79, 134, 190],
                      line_width_min_pixels=1.5, get_line_width=2, pickable=False),
            # reach severity zones — translucent, rounded
            pdk.Layer("PathLayer", _reaches, get_path="path", get_color="color",
                      get_width="width", width_units="pixels", width_min_pixels=5,
                      width_max_pixels=16, cap_rounded=True, joint_rounded=True, pickable=True),
            # sensor nodes
            pdk.Layer("ScatterplotLayer", _nodes, get_position="position",
                      get_fill_color="color", get_radius=160, radius_min_pixels=7,
                      radius_max_pixels=13, stroked=True, get_line_color=[255, 255, 255],
                      line_width_min_pixels=2, pickable=True),
            # stream-name labels at reach midpoints
            pdk.Layer("TextLayer", _rlabels, get_position="position", get_text="text",
                      get_size=13, get_color=[19, 33, 46], get_pixel_offset=[0, -16],
                      get_alignment_baseline="'bottom'", get_text_anchor="'middle'",
                      font_weight=600, background=True,
                      get_background_color=[255, 255, 255, 205],
                      background_padding=[6, 2, 6, 2], pickable=False),
            # node name + depth/discharge labels
            pdk.Layer("TextLayer", _nodes, get_position="position", get_text="label",
                      get_size=11, get_color=[19, 33, 46], get_pixel_offset=[0, 16],
                      get_alignment_baseline="'top'", get_text_anchor="'middle'",
                      font_weight=700, background=True,
                      get_background_color=[255, 255, 255, 225],
                      background_padding=[6, 3, 6, 3], pickable=False),
        ]
        _view = pdk.ViewState(latitude=35.262, longitude=-83.197, zoom=11.3, bearing=0, pitch=0)
        _deck = pdk.Deck(layers=_layers, initial_view_state=_view, map_style=None,
                         tooltip={"text": "{name}\n{tip}"})
        st.pydeck_chart(_deck, use_container_width=True)
    except Exception as _map_err:
        st.info(f"Map unavailable: {_map_err}")

    # legend + provenance
    _leg = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;margin-right:16px;">'
        f'<span style="width:13px;height:13px;border-radius:3px;background:{SEV[L]};"></span>'
        f'<span style="font-size:0.8rem;color:#5B6B7A;">{L.title()}</span></span>' for L in ORDER)
    st.markdown(
        f'<div style="margin-top:10px;">{_leg}'
        '<span style="font-size:0.8rem;color:#8A97A4;">&middot; line thickness = discharge</span></div>'
        '<div style="font-size:0.74rem;color:#8A97A4;margin-top:6px;line-height:1.45;">'
        'Topographic basemap &amp; streams: Esri / USGS. Surveyed watershed boundary and campus outlet are '
        'real (StreamStats); upstream node pins are approximate (set to sensor GPS); reaches are straight '
        'connectors until the channel centerline is loaded. Cullowhee Creek is the USGS main stem; '
        'tributary names are placeholders to confirm.</div>', unsafe_allow_html=True)

with st.expander("Reach detail \u2014 depth, discharge, drainage area"):
    _rows = flood_profile.reaches()
    st.dataframe(
        [{'Stream': r['stream'], 'Reach': r['name'], 'Level': r['level'], 'Length (mi)': r['length_mi'],
          'Depth up\u2192dn (ft)': f"{r['up_depth_ft']:.1f} \u2192 {r['dn_depth_ft']:.1f}",
          'Discharge up\u2192dn (cfs)': f"{r['up_discharge_cfs']:,} \u2192 {r['dn_discharge_cfs']:,}",
          'Drainage area up\u2192dn (mi\u00b2)': f"{r['up_area_sqmi']} \u2192 {r['dn_area_sqmi']}"} for r in _rows],
        use_container_width=True, hide_index=True)

with st.expander("Schematic view (no basemap)"):
    components.html(
        '<div style="font-family:Inter,system-ui,sans-serif">' + flood_profile.corridor_svg() + '</div>',
        height=600, scrolling=False)

st.markdown('<div class="eyebrow">Incoming weather — forecast precipitation</div>', unsafe_allow_html=True)
fc = fetch_best_7day(); days = fc.get("days", [])
if days:
    tiles = []
    for i, d in enumerate(days):
        hi = f'{d["hi"]}°' if d.get("hi") is not None else "--"
        lo = f'{d["lo"]}°' if d.get("lo") is not None else "--"
        pop = d.get("pop", 0); src = d.get("source", "—")
        cls = "ftile ftile-today" if i == 0 else "ftile"
        tiles.append(
            f'<div class="{cls}"><div class="ftile-day">{d["label"]}</div>'
            f'<div style="font-size:1.4rem;line-height:1.3;">{weather_emoji(d.get("code",0))}</div>'
            f'<div class="ftile-hi">{hi}</div><div class="ftile-lo">lo {lo}</div>'
            f'<div class="ftile-desc">{d.get("desc","—")}</div>'
            f'<div style="background:#E5E9ED;border-radius:3px;height:4px;margin:5px 3px;">'
            f'<div style="height:100%;width:{pop}%;background:{pop_color(pop)};border-radius:3px;"></div></div>'
            f'<div class="ftile-day">PoP {pop}%</div>'
            f'<div class="ftile-src" style="color:{SRC_COLOR.get(src,"#888")};">{src}</div></div>')
    st.markdown(f'<div class="grid fcast">{"".join(tiles)}</div>', unsafe_allow_html=True)
    st.markdown('<div class="site-detail" style="color:#8A97A4;margin-top:6px;">'
                'Model by horizon: HRRR (0–1 d) · NWS (2–3 d) · ECMWF (4–6 d)</div>',
                unsafe_allow_html=True)
    if fc.get("errors"):
        st.markdown('<div class="site-detail" style="color:#8A97A4;">Model notes: '
                    + " · ".join(f"{k}: {v[:36]}" for k, v in fc["errors"].items()) + '</div>',
                    unsafe_allow_html=True)
else:
    st.info("Forecast unavailable right now.")

# stage records
for sid, inp in inputs.items():
    series = inp.get("stage_series")
    if series:
        name = flood_network.SITES[sid]["name"]
        st.markdown(f'<div class="eyebrow">{name} — stage record</div>', unsafe_allow_html=True)
        sdf = pd.DataFrame({f"{name} stage (ft)": [s for _, s in series]},
                           index=[datetime.fromtimestamp(t, TIMEZONE) for t, _ in series])
        st.line_chart(sdf, height=200)

# system status / provenance
st.markdown('<div class="eyebrow">System status — data sources & confidence</div>', unsafe_allow_html=True)

def chip(label, status):
    c = STATUS_COLOR.get(status, "#888")
    return (f'<span class="chip" style="background:{c}14;color:{c};border-color:{c}55;">'
            f'{label}: {status}</span>')

def site_status(sid, key):
    d = inputs.get(sid, {})
    return ("synthetic" if demo else "live") if d.get(key) is not None else "none"

rows = []
for sid in ["belk"] + flood_network.contributing_sites("belk"):
    name = flood_network.SITES[sid]["name"]
    parts = [chip("rain", site_status(sid, "storm_rain_in")),
             chip("soil", site_status(sid, "soil_pct")),
             chip("stage", site_status(sid, "stage_series"))]
    if sid in orographic.TERRAIN:
        parts.append(chip("lift", ("synthetic" if demo else "live") if sid in oro else "none"))
    rows.append(f'<div class="chiprow"><span class="chip-label">{name}</span>{"".join(parts)}</div>')
st.markdown("".join(rows), unsafe_allow_html=True)
st.markdown('<div class="chiprow" style="margin-top:6px;"><span class="chip-label">Model parameters</span>'
            + "".join(chip(i.replace("_", " "), s) for i, s, _ in flood_network.describe_provenance())
            + '</div>', unsafe_allow_html=True)

# footer
st.markdown(f"""
<div class="footer">
  <div><b>Methodology.</b> HDc-corrected Manning's discharge · TR-55 dynamic curve number ·
  kinematic-wave channel routing · orographic lift index. A sub-watershed network routes
  upstream sub-basins (Double Springs, AAHP) to the WCU Campus (Camp Building) warning
  point with travel-time lead.</div>
  <div style="margin-top:6px;"><b>Channel capacity (Manning–HDc):</b>
  <span class="mono">7 ft = {flood_engine.mannings_discharge_cfs(7):,.0f} cfs ·
  9 ft = {flood_engine.mannings_discharge_cfs(9):,.0f} cfs ·
  11 ft = {flood_engine.mannings_discharge_cfs(11):,.0f} cfs</span></div>
  <div style="margin-top:6px;"><b>Data sources.</b> NWS · HRRR · ECMWF forecast · USGS gauges ·
  in-network LoRa sensors (rain, soil moisture, stream stage).</div>
  <div class="disclaimer">Research prototype developed at Western Carolina University.
  This demonstration uses synthetic data and provisional parameters pending field calibration.
  Not an operational warning service and not for life-safety decisions.</div>
</div>
""", unsafe_allow_html=True)
