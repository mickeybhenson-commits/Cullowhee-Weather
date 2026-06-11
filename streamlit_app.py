"""
firestore_bme280.py
===================================================================
Drop-in BME280 history panel for the WCU Belk Weather Intelligence
Streamlit dashboard. Pulls temp / humidity / pressure time series from
Firestore and renders dark-HUD Plotly line charts that match app.py.

-------------------------------------------------------------------
WIRING (three edits to app.py):

  1. At the top of app.py, add:
         from firestore_bme280 import render_bme280_history_panel

  2. Anywhere you want the panel (e.g. right after the 7-DAY FORECAST
     PANEL block, before the RADAR MAP PANEL), add one line:
         render_bme280_history_panel()

  3. requirements.txt — add:
         google-cloud-firestore

-------------------------------------------------------------------
AUTH:
  Local dev   -> Application Default Credentials (gcloud auth
                 application-default login).
  Streamlit   -> paste your service-account JSON into the app's
   Community     Secrets as a [gcp_service_account] table, e.g.:

     [gcp_service_account]
     type = "service_account"
     project_id = "ee-dashboard-477704"
     private_key_id = "..."
     private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
     client_email = "...@ee-dashboard-477704.iam.gserviceaccount.com"
     client_id = "..."
     token_uri = "https://oauth2.googleapis.com/token"

-------------------------------------------------------------------
CONFIRM BEFORE DEPLOY (the only things I assumed):
  - COLLECTION  -> "station_1" (one of the fragmented buckets; switch
                   to "skye_belk_thp1" once the reflash is confirmed).
  - METRICS     -> field keys default to the OBSERVED schema
                   (temp_f / humidity / pressure_inhg). If your node
                   writes temp_c / pressure_hpa, change them here.
  - TIME_FIELD  -> "timestamp"; falls back to Firestore create_time
                   automatically if that field is absent or unparseable.
===================================================================
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st
from google.cloud import firestore

# ------------------------------------------------------------------
# CONFIG  — edit these if the schema differs
# ------------------------------------------------------------------

PROJECT_ID = "ee-dashboard-477704"
DATABASE   = "cullowhee"
COLLECTION = "station_1"          # <- flip to "skye_belk_thp1" post-reflash
TIME_FIELD = "timestamp"          # <- falls back to doc.create_time if missing
SENTINEL   = -1                   # your "missing field" marker
TIMEZONE   = ZoneInfo("America/New_York")

# Field keys default to what was actually observed in the station_1 doc.
# If the node sends temp_c / pressure_hpa instead, change "key" below.
METRICS = [
    {"key": "temp_f",        "label": "TEMPERATURE", "unit": "°F",   "color": "#00FF9C"},
    {"key": "humidity",      "label": "HUMIDITY",    "unit": "%",    "color": "#5AC8FA"},
    {"key": "pressure_inhg", "label": "PRESSURE",    "unit": " inHg", "color": "#FFD700"},
]

# Optional 4th trace if you want it — battery is in the same doc.
# METRICS.append({"key": "battery_v", "label": "BATTERY", "unit": " V", "color": "#AAFF00"})

PALETTE = {"text": "#E0E8F0", "muted": "#7AACCC",
           "grid": "rgba(0,136,255,0.08)", "axis": "#2A4060"}

MAX_DOCS = 3000   # safety cap on how many docs to pull per refresh

# ------------------------------------------------------------------
# CLIENT
# ------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _get_db():
    """Firestore client for the named 'cullowhee' database."""
    if "gcp_service_account" in st.secrets:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"])
        )
        return firestore.Client(project=PROJECT_ID, database=DATABASE, credentials=creds)
    # local dev: application default credentials
    return firestore.Client(project=PROJECT_ID, database=DATABASE)

# ------------------------------------------------------------------
# TIME PARSING
# ------------------------------------------------------------------

def _extract_time(rec, doc):
    """
    Resolve a tz-aware local datetime for a document.
    Tries the payload TIME_FIELD (datetime / epoch / ISO string),
    then falls back to Firestore's own create_time.
    """
    raw = rec.get(TIME_FIELD)
    dt = None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, (int, float)) and raw > 0:
        secs = raw / 1000.0 if raw > 1e12 else float(raw)   # ms vs s
        try:
            dt = datetime.fromtimestamp(secs, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            dt = None
    elif isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            dt = None

    if dt is None:
        ct = getattr(doc, "create_time", None)   # every Firestore doc has this
        if ct is not None:
            dt = ct

    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TIMEZONE)

# ------------------------------------------------------------------
# FETCH
# ------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def fetch_bme280_history(collection: str, hours, max_docs: int = MAX_DOCS):
    """
    Returns a chronological list of (datetime, doc_dict) tuples.
    `hours=None` returns everything available (capped at max_docs).
    """
    db = _get_db()
    col = db.collection(collection)

    # Prefer server-side ordering; fall back to an unordered limited stream
    # (handles the case where TIME_FIELD doesn't exist on the docs).
    docs = []
    try:
        q = col.order_by(TIME_FIELD, direction=firestore.Query.DESCENDING).limit(max_docs)
        docs = list(q.stream())
    except Exception:
        docs = []
    if not docs:
        docs = list(col.limit(max_docs).stream())

    cutoff = (datetime.now(TIMEZONE) - timedelta(hours=hours)) if hours else None
    rows = []
    for d in docs:
        rec = d.to_dict() or {}
        ts = _extract_time(rec, d)
        if ts is None:
            continue
        if cutoff and ts < cutoff:
            continue
        rows.append((ts, rec))

    rows.sort(key=lambda r: r[0])
    return rows


def _series(rows, key):
    """Extract (xs, ys) for one field, dropping the -1 sentinel."""
    xs, ys = [], []
    for ts, rec in rows:
        v = rec.get(key)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v == SENTINEL:
            continue
        xs.append(ts)
        ys.append(v)
    return xs, ys

# ------------------------------------------------------------------
# CHART
# ------------------------------------------------------------------

def _make_ts_fig(xs, ys, metric):
    color = metric["color"]
    unit  = metric["unit"]
    last  = ys[-1] if ys else None

    fig = go.Figure(go.Scatter(
        x=xs, y=ys, mode="lines+markers",
        line=dict(color=color, width=2, shape="spline", smoothing=0.5),
        marker=dict(size=3, color=color),
        fill="tozeroy" if metric["key"] == "humidity" else None,
        fillcolor="rgba(90,200,250,0.06)",
        hovertemplate="%{x|%m/%d %H:%M}<br>%{y:.2f}" + unit + "<extra></extra>",
    ))

    title = f"{metric['label']}"
    if last is not None:
        title += f"  —  now {last:.1f}{unit}"
        lo, hi = min(ys), max(ys)
        title += f"   ·   range {lo:.1f}–{hi:.1f}{unit}"

    fig.update_layout(
        title=dict(text=title, font=dict(size=12, color=PALETTE["muted"],
                                         family="Share Tech Mono"), x=0.01, xanchor="left"),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=PALETTE["text"], family="Rajdhani"),
        margin=dict(t=34, b=24, l=48, r=18), height=230, showlegend=False,
        hovermode="x unified",
        xaxis=dict(showgrid=True, gridcolor=PALETTE["grid"], linecolor=PALETTE["axis"],
                   tickfont=dict(color="#5A7A9A", size=9), tickformat="%m/%d\n%H:%M"),
        yaxis=dict(showgrid=True, gridcolor=PALETTE["grid"], linecolor=PALETTE["axis"],
                   tickfont=dict(color="#5A7A9A", size=9), zeroline=False,
                   ticksuffix=unit.strip() if unit.strip() in ("%",) else ""),
    )
    return fig

# ------------------------------------------------------------------
# PANEL
# ------------------------------------------------------------------

def render_bme280_history_panel():
    st.markdown(
        '<div class="panel"><div class="panel-title">'
        f'📈 BME280 Sensor History &nbsp;·&nbsp; {COLLECTION} '
        '<span class="source-badge">FIRESTORE</span></div>',
        unsafe_allow_html=True,
    )

    window = st.radio(
        "Window", ["24 h", "7 d", "30 d", "All"],
        index=1, horizontal=True, label_visibility="collapsed",
        key="bme280_window",
    )
    hours = {"24 h": 24, "7 d": 168, "30 d": 720, "All": None}[window]

    try:
        rows = fetch_bme280_history(COLLECTION, hours)
    except Exception as e:
        st.warning(f"Firestore unavailable: {e}")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    if not rows:
        st.info(f"No documents found in '{COLLECTION}' for the selected window.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    any_plotted = False
    for metric in METRICS:
        xs, ys = _series(rows, metric["key"])
        if not xs:
            st.markdown(
                f"<div class='small-muted'>No '{metric['key']}' values in this window "
                f"(field missing or all sentinel).</div>",
                unsafe_allow_html=True,
            )
            continue
        any_plotted = True
        st.plotly_chart(_make_ts_fig(xs, ys, metric),
                        use_container_width=True, config={"displayModeBar": False})

    last_ts = rows[-1][0].strftime("%a %m/%d %I:%M %p")
    st.markdown(
        f"<div class='small-muted' style='text-align:right;'>"
        f"{len(rows)} readings &nbsp;·&nbsp; latest {last_ts} "
        f"&nbsp;·&nbsp; SRC: cullowhee/{COLLECTION}</div>",
        unsafe_allow_html=True,
    )
    if not any_plotted:
        st.warning("Documents found, but none of the configured field keys matched. "
                   "Check the METRICS field names against an actual document.")

    st.markdown("</div>", unsafe_allow_html=True)
