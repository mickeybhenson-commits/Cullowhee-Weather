"""
streamlit_app.py  —  Belk station_1: temperature, humidity, pressure
=====================================================================
Pulls ONLY temp / humidity / pressure from the station_1 collection
in the 'cullowhee' Firestore database and graphs them. Pressure is
corrected to sea level in Python (display-side); the raw value stays
untouched in Firestore.
=====================================================================
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from google.cloud import firestore

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
PROJECT_ID = "ee-dashboard-477704"
DATABASE   = "cullowhee"
COLLECTION = "station_1"            # the live Belk bucket right now
TIMEZONE   = ZoneInfo("America/New_York")
SENTINEL   = -1                     # treated as "missing"

# Belk rooftop elevation, used ONLY for the sea-level pressure correction.
# Set this to your surveyed rooftop value for an accurate barometer reading.
ELEVATION_FT = 2100.0

st.set_page_config(page_title="Belk · T/H/P", layout="wide")
st.title("Belk Station — Temperature · Humidity · Pressure")
st.caption(f"{COLLECTION} · cullowhee · {PROJECT_ID}")

# ---------------------------------------------------------------------
# CONNECT
# ---------------------------------------------------------------------
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
# CORRECTION  (display-side only)
# ---------------------------------------------------------------------
def sea_level_inhg(station_inhg, temp_f, elevation_ft):
    """
    Reduce raw STATION pressure to sea-level-equivalent pressure.
    Uses the standard barometric reduction with live station temp.
    Input/output in inHg. Raw value is never modified in Firestore.
    """
    if station_inhg is None:
        return None
    h = elevation_ft * 0.3048                       # ft -> m
    p_hpa = station_inhg * 33.8639                  # inHg -> hPa
    t_c = (temp_f - 32.0) * 5.0 / 9.0 if temp_f is not None else 15.0
    factor = (1.0 - (0.0065 * h) / (t_c + 0.0065 * h + 273.15)) ** (-5.257)
    return (p_hpa * factor) / 33.8639               # hPa -> inHg

# ---------------------------------------------------------------------
# TIME PARSING
#   station_1's "timestamp" is stored as the *text* of a
#   DatetimeWithNanoseconds(...) object, so we recover the real reading
#   time by parsing those integers; fall back to Firestore create_time.
# ---------------------------------------------------------------------
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
# FETCH
# ---------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner="Pulling station_1…")
def fetch(elevation_ft, max_docs=2000):
    db = get_db()
    docs = list(db.collection(COLLECTION).limit(max_docs).stream())
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
# UI
# ---------------------------------------------------------------------
c1, c2 = st.columns([1, 1])
window = c1.radio("Window", ["24 h", "7 d", "30 d", "All"],
                  index=1, horizontal=True)
correct = c2.toggle("Sea-level pressure correction", value=True)

df = fetch(ELEVATION_FT)
if df.empty:
    st.warning("No readings returned from station_1.")
    st.stop()

hours = {"24 h": 24, "7 d": 168, "30 d": 720, "All": None}[window]
if hours:
    cutoff = datetime.now(TIMEZONE) - timedelta(hours=hours)
    df = df[df.index >= cutoff]

if df.empty:
    st.info("No readings in the selected window — widen it.")
    st.stop()

st.write(f"**{len(df)}** readings · latest {df.index[-1].strftime('%a %m/%d %I:%M %p')}")

# Temperature
st.subheader("Temperature")
st.line_chart(df[["Temperature (°F)"]], height=240)

# Humidity
st.subheader("Humidity")
st.line_chart(df[["Humidity (%)"]], height=240)

# Pressure (raw vs corrected)
st.subheader("Pressure")
pcol = "Pressure sea-level (inHg)" if correct else "Pressure raw (inHg)"
st.caption("Sea-level-corrected in Python · raw value preserved in Firestore"
           if correct else "Raw station pressure (uncorrected for elevation)")
st.line_chart(df[[pcol]], height=240)

with st.expander("Raw table"):
    st.dataframe(df)
