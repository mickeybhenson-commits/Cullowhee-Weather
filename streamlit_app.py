"""
streamlit_app.py  —  Belk dashboard + watershed flood network
=====================================================================
Top    : temp / humidity / pressure from station_1 (pressure corrected
         in Python; raw preserved in Firestore).
Bottom : routed watershed warning for Belk via flood_network —
         Belk's combined warning + each UPSTREAM site (Double Springs,
         AAHP) with status and travel-time ETA. Body Farm is excluded
         automatically (it drains below Belk).

Live inputs are wired per-site as sensors come online; until then a
DEMO scenario injects a synthetic upstream pulse so the routing is
visible end-to-end.
=====================================================================
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from google.cloud import firestore

try:
    import flood_engine
    import flood_network
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

THP_COLLECTION = "station_1"
ELEVATION_FT   = 2100.0          # Belk rooftop [CONFIRM]

LEVEL_COLOR = {"NORMAL": "#1D9E75", "WATCH": "#EF9F27",
               "WARNING": "#D85A30", "EMERGENCY": "#E24B4A"}

def prob_color(p):
    return ("#1D9E75" if p < 0.30 else "#EF9F27" if p < 0.60
            else "#D85A30" if p < 0.85 else "#E24B4A")

st.set_page_config(page_title="Belk · weather + watershed", layout="wide")

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
# FETCH — per-site stage series (real, generic)
# ---------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_stage_series(collection, max_docs=2000):
    if not collection:
        return None
    try:
        db = get_db()
        docs = list(db.collection(collection).limit(max_docs).stream())
    except Exception:
        return None
    rows = []
    for d in docs:
        rec = d.to_dict() or {}
        t = parse_time(rec.get("timestamp"), d)
        s = _clean(rec.get("stage_ft"))
        if t is None or s is None:
            continue
        rows.append((int(t.timestamp()), s))
    rows.sort()
    return rows or None

def assemble_live_inputs():
    """Pull whatever real inputs exist for each site. All None for now."""
    inputs = {}
    for sid, s in flood_network.SITES.items():
        d = {}
        series = fetch_stage_series(s.get("stage_coll"))
        if series:
            d["stage_series"] = series
        if d:
            inputs[sid] = d
    return inputs

def demo_inputs():
    """Synthetic upstream pulse anchored to 'now' (scenario A)."""
    now = datetime.now(TIMEZONE)
    step, hours = 5, 3
    n = (hours * 60) // step
    base, peak = 4.0, 9.5
    rising = []
    for k in range(n):
        t = now - timedelta(minutes=(n - k) * step)
        frac = (k + 1) / n
        rising.append((int(t.timestamp()), round(base + (peak - base) * (frac ** 1.6), 3)))
    return {
        "double_springs": {"stage_series": rising},          # upstream surging
        "aahp": {"soil_pct": 88.0, "storm_rain_in": 1.8},     # primed, rain-only
    }

# =====================================================================
# UI — ATMOSPHERIC
# =====================================================================
st.title("Belk Station — weather + watershed flood network")
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
        st.line_chart(df[["Temperature (°F)"]], height=220)
        st.subheader("Humidity")
        st.line_chart(df[["Humidity (%)"]], height=220)
        st.subheader("Pressure")
        pcol = "Pressure sea-level (inHg)" if correct else "Pressure raw (inHg)"
        st.caption("Sea-level-corrected in Python · raw value preserved in Firestore"
                   if correct else "Raw station pressure (uncorrected for elevation)")
        st.line_chart(df[[pcol]], height=220)

# =====================================================================
# UI — WATERSHED FLOOD NETWORK
# =====================================================================
st.divider()
st.header("Watershed flood network — Belk")

if not FLOOD_OK:
    st.error(f"flood modules could not be imported: {_FLOOD_ERR}")
else:
    live = assemble_live_inputs()
    has_live = any(live.values())
    demo = st.toggle("Demo scenario (synthetic upstream pulse)", value=not has_live)
    inputs = demo_inputs() if demo else live

    if demo:
        st.info("DEMO — synthetic upstream pulse. No live stage/rain/soil sensors "
                "are reporting yet; this shows the routing end-to-end. Turn off once "
                "real per-site inputs are flowing.")

    rw = flood_network.routed_assessment("belk", inputs)

    # headline banner
    pc = prob_color(rw.combined_probability)
    lead = f"{rw.lead_time_hr} hr" if rw.lead_time_hr is not None else "—"
    st.markdown(
        f"<div style='background:{pc}22;border-left:6px solid {pc};border-radius:0 8px 8px 0;"
        f"padding:12px 18px;margin-bottom:6px;'>"
        f"<span style='font-size:1.1em;color:{pc};font-weight:600;'>BELK ROUTED OUTLOOK</span><br>"
        f"<span style='font-size:1.6em;font-weight:700;color:{pc};'>"
        f"{rw.combined_probability:.0%}</span>"
        f"<span style='color:#9aa;'> combined warning probability · lead time {lead}</span>"
        f"</div>", unsafe_allow_html=True)
    st.caption(rw.note)

    # site cards
    st.subheader("Contributing sites")
    cards = st.columns(1 + len(rw.upstream))

    with cards[0]:
        if rw.local:
            lc = LEVEL_COLOR.get(rw.local.level, "#888")
            st.markdown(f"**Belk** (outlet)")
            st.markdown(f"<span style='color:{lc};font-weight:700;'>{rw.local.level}</span> · "
                        f"{rw.local.stage_ft} ft", unsafe_allow_html=True)
            st.caption(f"discharge {rw.local.discharge_cfs:,.0f} cfs")
        else:
            st.markdown("**Belk** (outlet)")
            st.markdown("<span style='color:#888;'>no stage gauge yet</span>",
                        unsafe_allow_html=True)
            st.caption("warning relies on upstream until depth node is online")

    for col, c in zip(cards[1:], rw.upstream):
        with col:
            st.markdown(f"**{c.name}** ↑")
            if c.level is not None:
                lc = LEVEL_COLOR.get(c.level, "#888")
                st.markdown(f"<span style='color:{lc};font-weight:700;'>{c.level}</span> · "
                            f"P {c.ew_prob:.0%}", unsafe_allow_html=True)
            elif c.priming is not None:
                st.markdown(f"priming **{c.priming:.0%}**")
            else:
                st.markdown("<span style='color:#888;'>no inputs online</span>",
                            unsafe_allow_html=True)
            st.caption(f"~{c.eta_hr} hr travel time to Belk")

    st.caption("Body Farm is excluded — it enters the stream below Belk and cannot affect it.")

    # show any site with a live/demo stage series
    for sid, inp in inputs.items():
        series = inp.get("stage_series")
        if series:
            name = flood_network.SITES[sid]["name"]
            sdf = pd.DataFrame(
                {f"{name} stage (ft)": [s for _, s in series]},
                index=[datetime.fromtimestamp(t, TIMEZONE) for t, _ in series],
            )
            st.subheader(f"{name} stage")
            st.line_chart(sdf, height=220)

    st.caption(
        f"Manning's-HDc discharge at Belk thresholds: "
        f"7 ft = {flood_engine.mannings_discharge_cfs(7):,.0f} · "
        f"9 ft = {flood_engine.mannings_discharge_cfs(9):,.0f} · "
        f"11 ft = {flood_engine.mannings_discharge_cfs(11):,.0f} cfs   "
        f"(HDc = {flood_engine.HDC}; warning probability uncalibrated — relative, not absolute)"
    )
