"""
streamlit_app.py  —  minimal Firestore pull + graph test
===========================================================
Standalone diagnostic app. Does ONE thing: connect to the
'cullowhee' Firestore database, read a collection, show what's
in it, and graph the numeric fields.

AUTH (Streamlit Cloud Secrets):
  Works with EITHER style of [gcp_service_account] secret:
    Style 1 (blob):
        [gcp_service_account]
        json = '''
        { ...entire JSON key file pasted here... }
        '''
    Style 2 (field-by-field table):
        [gcp_service_account]
        type = "service_account"
        project_id = "ee-dashboard-477704"
        private_key_id = "..."
        private_key = "..."
        client_email = "..."
        client_id = "..."
        token_uri = "https://oauth2.googleapis.com/token"
===========================================================
"""

import pandas as pd
import streamlit as st
from google.cloud import firestore

PROJECT_ID = "ee-dashboard-477704"
DATABASE   = "cullowhee"

st.set_page_config(page_title="Firestore Test", layout="wide")
st.title("Firestore Pull Test")
st.caption(f"project: {PROJECT_ID}  |  database: {DATABASE}")

# ----------------------------------------------------------
# 1. CONNECT
# ----------------------------------------------------------
st.subheader("1. Connection")

def get_db():
    if "gcp_service_account" in st.secrets:
        import json
        from google.oauth2 import service_account
        sa = st.secrets["gcp_service_account"]
        # Style 1: a single 'json' blob  |  Style 2: a field-by-field table
        info = json.loads(sa["json"]) if "json" in sa else dict(sa)
        creds = service_account.Credentials.from_service_account_info(info)
        st.write("Using service-account credentials from Secrets.")
        return firestore.Client(project=PROJECT_ID, database=DATABASE, credentials=creds)
    st.write("No [gcp_service_account] in Secrets — trying default credentials.")
    return firestore.Client(project=PROJECT_ID, database=DATABASE)

try:
    db = get_db()
    st.success("Connected to Firestore client OK.")
except Exception as e:
    st.error("Could NOT create the Firestore client.")
    st.exception(e)
    st.stop()

# ----------------------------------------------------------
# 2. CHOOSE COLLECTION + PULL
# ----------------------------------------------------------
st.subheader("2. Pull a collection")

collection = st.text_input("Collection name", value="station_1")
limit = st.number_input("Max documents to pull", min_value=10, max_value=5000, value=500, step=50)

if st.button("Pull data", type="primary"):
    try:
        docs = list(db.collection(collection).limit(int(limit)).stream())
    except Exception as e:
        st.error(f"Query against '{collection}' failed.")
        st.exception(e)
        st.stop()

    st.write(f"Documents returned from **{collection}**: **{len(docs)}**")
    if not docs:
        st.warning("Zero documents. Check the collection name (Firestore is case-sensitive: "
                   "'station_1', 'Station_1', and 'LoRa_Station_1' are all different).")
        st.stop()

    # ------------------------------------------------------
    # 3. SHOW THE RAW FIELDS  (this is how we learn the real keys)
    # ------------------------------------------------------
    st.subheader("3. What one document actually contains")
    first = docs[0].to_dict() or {}
    st.json(first)

    # build a row per doc, attaching a usable time + Firestore create_time
    rows = []
    for d in docs:
        rec = dict(d.to_dict() or {})
        rec["_create_time"] = getattr(d, "create_time", None)
        rec["_doc_id"] = d.id
        rows.append(rec)

    df = pd.DataFrame(rows)

    # ------------------------------------------------------
    # 4. PICK A TIME AXIS
    # ------------------------------------------------------
    st.subheader("4. Time axis")
    time_candidates = [c for c in df.columns
                       if c in ("timestamp", "time", "datetime", "created", "ts", "_create_time")]
    time_col = st.selectbox(
        "Field to use as time (x-axis)",
        options=time_candidates or ["_create_time"],
        index=0,
    )
    df["_t"] = pd.to_datetime(df[time_col], errors="coerce", utc=True)
    df = df.dropna(subset=["_t"]).sort_values("_t")
    try:
        df["_t"] = df["_t"].dt.tz_convert("America/New_York")
    except Exception:
        pass
    st.write(f"Rows with a valid time: **{len(df)}**")

    # ------------------------------------------------------
    # 5. GRAPH NUMERIC FIELDS
    # ------------------------------------------------------
    st.subheader("5. Graphs")
    numeric_cols = [
        c for c in df.columns
        if c not in ("_t", "_create_time", "_doc_id")
        and pd.api.types.is_numeric_dtype(pd.to_numeric(df[c], errors="coerce"))
    ]
    if not numeric_cols:
        st.warning("No numeric fields detected to graph. The raw document above shows what's there.")
    else:
        chosen = st.multiselect(
            "Fields to graph", options=numeric_cols,
            default=[c for c in ("temp_f", "temp_c", "humidity",
                                  "pressure_inhg", "pressure_hpa")
                     if c in numeric_cols] or numeric_cols[:3],
        )
        for col in chosen:
            series = pd.to_numeric(df[col], errors="coerce")
            series = series.replace(-1, pd.NA)   # treat -1 sentinel as a gap
            plot_df = pd.DataFrame({col: series.values}, index=df["_t"].values)
            st.markdown(f"**{col}**")
            st.line_chart(plot_df, height=220)

    # ------------------------------------------------------
    # 6. RAW TABLE
    # ------------------------------------------------------
    with st.expander("See raw table of all pulled documents"):
        st.dataframe(df.drop(columns=["_create_time"], errors="ignore"))
else:
    st.info("Set a collection name above and click **Pull data**.")
