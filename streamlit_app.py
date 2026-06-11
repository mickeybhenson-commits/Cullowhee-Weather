"""
streamlit_app.py  —  Belk dashboard + flood engine
=====================================================================
Top half  : temp / humidity / pressure pulled from station_1
            (pressure sea-level-corrected in Python; raw kept in Firestore).
Bottom half: flood_engine running on stage data.

Stage source: tries STAGE_COLLECTION first; if that collection is empty
or absent (Argonaut node not online yet), it falls back to a synthetic
hydrograph so the engine is visible end-to-end. When real depth starts
landing in STAGE_COLLECTION, the dashboard switches to it automatically.
=====================================================================
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from google.cloud import firestore

# flood logic lives in its own importable module beside this file
try:
    import flood_engine
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

# --- atmospheric node (BME280) ---
THP_COLLECTION = "station_1"
ELEVATION_FT   = 2100.0          # Belk rooftop; set surveyed value [CONFIRM]

# --- stage node (Argonaut-SW) — not online yet ---
STAGE_COLLECTION = "noah_stage"  # [CONFIRM] collection the depth node will write to
STAGE_FIELD      = "stage_ft"    # [CONFIRM] depth field name in that doc

LEVEL_COLOR = {"NORMAL": "#1D9E75", "WATCH": "#EF9F27",
               "WARNING": "#D85A30", "EMERGENCY": "#E24B4A"}

st.set_page_config(page_title="Belk · weather + flood", layout="wide")

# ---------------------------------------------------------------------
# CONNECT
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

# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------
def sea_level_inhg(station_inhg, temp_f, elevation_ft):
    if station_inhg is None:
        return None
    h = elevation_ft * 0.3048
    p_hpa = station_inhg * 33.8639
    t_c = (temp_f - 32.0) * 5.0 / 9.0 if temp_f is not None else 15.0
    factor = (1.0 - (0.0065 * h) / (t_c + 0.0065 * h + 273.15)) ** (-5.257)
    return (p_hpa * factor) / 33.8639

_DT_RE = re.compile(
    r"DatetimeWithNanoseconds\(\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),"
    r"\s*(\d+),\s*(\d+)(?:,\s*(\d+))?"
)

def parse_time(raw, doc):
    dt = None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        m = _DT_RE.search(raw)
        if m:
            parts = [int(x) for x in m.groups() if x is not None]
            try:
                dt = datetime(*parts, tzinfo=timezone.utc)
            except (ValueError, TypeError):
                dt = None
        if dt is None:
            try:
                dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            except ValueError:
                dt = None
    if dt is None:
        ct = getattr(doc, "create_time", None)
        if ct is not None:
            dt = ct
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TIMEZONE)

def _clean(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v == SENTINEL else v

# ---------------------------------------------------------------------
# FETCH — atmospheric
# ---------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner="Pulling station_1…")
def fetch_thp(elevation_ft, max_docs=2000):
    db = get_db()
    docs = list(db.collection(THP_COLLECTION).limit(max_docs).stream())
    rows = []
    for d in docs:
        rec = d.to_dict() or {}
        t = parse_time(rec.get("timestamp"), d)
        if t is None:
            continue
        temp = _clean(rec.get("temp_f"))
        hum  = _clean(rec.get("humidity"))
        praw = _clean(rec.get("pressure_inhg"))
        rows.append({
            "time": t,
            "Temperature (°F)": temp,
            "Humidity (%)": hum,
            "Pressure raw (inHg)": praw,
            "Pressure sea-level (inHg)": sea_level_inhg(praw, temp, elevation_ft),
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("time").set_index("time")

# ---------------------------------------------------------------------
# FETCH — stage  (real if available, else synthetic demo)
# ---------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_stage_real(max_docs=2000):
    try:
        db = get_db()
        docs = list(db.collection(STAGE_COLLECTION).limit(max_docs).stream())
    except Exception:
        return None
    rows = []
    for d in docs:
        rec = d.to_dict() or {}
        t = parse_time(rec.get("timestamp"), d)
        s = _clean(rec.get(STAGE_FIELD))
        if t is None or s is None:
            continue
        rows.append((int(t.timestamp()), s))
    rows.sort()
    return rows or None

def generate_demo_stage():
    """6 h synthetic hydrograph ending 'now': baseflow -> accelerating rise."""
    now = datetime.now(TIMEZONE)
    step, total = 5, 360
    n = total // step
    base, peak = 4.0, 9.3
    rise_start = n // 2
    pts = []
    for i in range(n + 1):
        t = now - timedelta(minutes=(n - i) * step)
        if i <= rise_start:
            stage = base
        else:
            frac = (i - rise_start) / (n - rise_start)
            stage = base + (peak - base) * (frac ** 1.6)
        pts.append((int(t.timestamp()), round(stage, 3)))
    return pts

# =====================================================================
# UI — ATMOSPHERIC
# =====================================================================
st.title("Belk Station — weather + flood engine")
st.caption(f"{THP_COLLECTION} · cullowhee · {PROJECT_ID}")

c1, c2 = st.columns([1, 1])
window = c1.radio("Window", ["24 h", "7 d", "30 d", "All"], index=1, horizontal=True)
correct = c2.toggle("Sea-level pressure correction", value=True)

df = fetch_thp(ELEVATION_FT)
if df.empty:
    st.warning("No readings returned from station_1.")
else:
    hours = {"24 h": 24, "7 d": 168, "30 d": 720, "All": None}[window]
    if hours:
        cutoff = datetime.now(TIMEZONE) - timedelta(hours=hours)
        df = df[df.index >= cutoff]

    if df.empty:
        st.info("No readings in the selected window — widen it.")
    else:
        st.write(f"**{len(df)}** readings · latest "
                 f"{df.index[-1].strftime('%a %m/%d %I:%M %p')}")
        st.subheader("Temperature")
        st.line_chart(df[["Temperature (°F)"]], height=240)
        st.subheader("Humidity")
        st.line_chart(df[["Humidity (%)"]], height=240)
        st.subheader("Pressure")
        pcol = "Pressure sea-level (inHg)" if correct else "Pressure raw (inHg)"
        st.caption("Sea-level-corrected in Python · raw value preserved in Firestore"
                   if correct else "Raw station pressure (uncorrected for elevation)")
        st.line_chart(df[[pcol]], height=240)

# =====================================================================
# UI — FLOOD ENGINE
# =====================================================================
st.divider()
st.header("Flood engine")

if not FLOOD_OK:
    st.error(f"flood_engine.py could not be imported: {_FLOOD_ERR}")
else:
    real = fetch_stage_real()
    if real:
        series, demo = real, False
    else:
        series, demo = generate_demo_stage(), True

    if demo:
        st.info("DEMO — synthetic stage hydrograph. The Argonaut depth node "
                f"isn't writing to `{STAGE_COLLECTION}` yet; this proves the engine "
                "end-to-end. It switches to live depth automatically when that data lands.")

    a = flood_engine.assess(series, prev_level="NORMAL",
                            soil_moisture_pct=None, storm_rain_in=None)
    color = LEVEL_COLOR.get(a.level, "#888780")

    st.markdown(
        f"<div style='background:{color}22;border-left:6px solid {color};"
        f"border-radius:0 8px 8px 0;padding:10px 16px;margin-bottom:12px;'>"
        f"<span style='font-size:1.4em;font-weight:600;color:{color};'>{a.level}</span>"
        f"</div>", unsafe_allow_html=True)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Stage", f"{a.stage_ft} ft")
    m2.metric("Discharge", f"{a.discharge_cfs:,.0f} cfs")
    m3.metric("Rate of rise", f"{a.rate_ft_hr:+.2f} ft/hr")
    m4.metric("Warning prob.", f"{a.ew_probability:.0%}")

    if a.next_level and a.time_to_next_hr is not None:
        st.caption(f"At current rate → **{a.next_level}** in ~{a.time_to_next_hr} hr")
    elif a.next_level:
        st.caption(f"Next level: {a.next_level} (not rising toward it)")

    stage_df = pd.DataFrame(
        {"Stage (ft)": [s for _, s in series]},
        index=[datetime.fromtimestamp(t, TIMEZONE) for t, _ in series],
    )
    st.line_chart(stage_df, height=260)

    st.caption(
        f"Manning's-HDc discharge at thresholds: "
        f"7 ft = {flood_engine.mannings_discharge_cfs(7):,.0f} · "
        f"9 ft = {flood_engine.mannings_discharge_cfs(9):,.0f} · "
        f"11 ft = {flood_engine.mannings_discharge_cfs(11):,.0f} cfs   "
        f"(HDc = {flood_engine.HDC} — placeholder until JAWRA value is set)"
    )
