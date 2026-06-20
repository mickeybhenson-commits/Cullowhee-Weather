"""
Test Model — Streamlit page for the Cullowhee flood-engine test model.
Imports test_model.py from the repo root (no logic duplicated here).
Adds a sub-basin map color-coded by live posture beneath the table.
"""

import json
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import test_model as tm

st.set_page_config(page_title="Cullowhee flood — test model", layout="wide")

POSTURE_COLOR = {"NORMAL": "#1D9E75", "WATCH": "#EF9F27",
                 "WARNING": "#D85A30", "EMERGENCY": "#A32D2D"}

# Fallback outlet locations (used only if cullowhee_subbasins.geojson is absent).
# Generate the real polygons with merge_subbasins.py for the proper choropleth.
BASIN_PTS = {
    "CC-UP-503":    (35.241, -83.185),
    "CC-MS-1100":   (35.276, -83.190),
    "CC-TIL-705":   (35.282, -83.200),
    "CC-SPD-1830":  (35.286, -83.183),
    "CC-COX-097":   (35.302, -83.178),
    "CC-LB-171":    (35.308, -83.192),
    "CC-WCU-2260":  (35.310, -83.187),
    "CC-MOUTH-2340":(35.317, -83.180),
}


def color_posture(series):
    return [f"background-color:{POSTURE_COLOR.get(v,'')};color:white;font-weight:600"
            if v in POSTURE_COLOR else "" for v in series]


def posture_map(postures):
    """folium map of the sub-basins colored by posture. Uses the real polygons
    from cullowhee_subbasins.geojson if present, else color-coded outlet markers."""
    m = folium.Map(location=[35.275, -83.190], zoom_start=12,
                   tiles="CartoDB positron", control_scale=True)
    geo = None
    try:
        with open("cullowhee_subbasins.geojson") as f:
            geo = json.load(f)
    except Exception:
        geo = None

    if geo:
        def style_fn(feat):
            bid = feat["properties"].get("basin_id")
            col = POSTURE_COLOR.get(postures.get(bid, "NORMAL"), "#888888")
            return {"fillColor": col, "color": "#333333", "weight": 1.2, "fillOpacity": 0.6}
        for feat in geo.get("features", []):
            bid = feat["properties"].get("basin_id", "")
            folium.GeoJson(
                feat, style_function=style_fn,
                tooltip=folium.Tooltip(f"{bid}: {postures.get(bid, '—')}")
            ).add_to(m)
    else:
        for bid, (lat, lon) in BASIN_PTS.items():
            col = POSTURE_COLOR.get(postures.get(bid, "NORMAL"), "#888888")
            folium.CircleMarker(
                [lat, lon], radius=11, color="#222222", weight=1,
                fill=True, fill_color=col, fill_opacity=0.9,
                tooltip=f"{bid}: {postures.get(bid, '—')}"
            ).add_to(m)
    return m, (geo is not None)


def legend():
    chips = "".join(
        f"<span style='display:inline-block;width:12px;height:12px;background:{c};"
        f"border-radius:2px;margin:0 4px 0 12px;vertical-align:-1px'></span>{name}"
        for name, c in POSTURE_COLOR.items())
    st.markdown(f"<div style='font-size:13px'>{chips}</div>", unsafe_allow_html=True)


st.title("Cullowhee Creek flood engine — tabletop test")
st.caption("Synthetic storms + antecedent moisture. No sensors required. "
           "Placeholders live in test_model.py — replace with confirmed values.")

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Storm")
    choices = list(tm.DESIGN_DEPTH_IN.keys()) + ["custom"]
    pick = st.radio("Design storm", choices, index=1)
    if pick == "custom":
        depth = st.slider("24-hr depth (in)", 0.5, 12.0, 4.8, 0.1)
    else:
        depth = tm.DESIGN_DEPTH_IN[pick]
        st.write(f"**{pick}** — {depth}\" / 24 hr")

    st.header("Antecedent moisture")
    p5 = st.slider("5-day antecedent rain (in)", 0.0, 5.0, 1.7, 0.1)
    arc = tm.arc_class(p5)
    arc_name = {1: "dry", 2: "normal", 3: "wet"}[arc]
    st.info(f"ARC class **{arc}** — {arc_name}")

    st.header("Tuning")
    prf = st.slider("Peak rate factor (PRF)", 300, 600, 484, 10,
                    help="~484 standard; raise toward 600 for steep mountain basins.")

# ---------------------------------------------------------------- tabs
tab1, tab2, tab3 = st.tabs(["Single case", "Storm × antecedent sweep", "Historical replay"])

with tab1:
    arc, res = tm.run_case(depth, p5, PRF=prf)
    rows = []
    for bid, r in res.items():
        b = tm.BASINS[bid]
        rows.append({"basin": bid, "DA (mi²)": b["DA"], "lead": b["lead"],
                     "CN": round(r["CN"]), "runoff (in)": round(r["Q"], 2),
                     "peak (cfs)": round(r["qp"]), "stage (ft)": round(r["stage"], 2),
                     "posture": r["posture"]})
    df = pd.DataFrame(rows)
    st.subheader(f"{depth}\" storm · ARC-{arc} · PRF {prf}")
    c = st.columns(4)
    for i, p in enumerate(["NORMAL", "WATCH", "WARNING", "EMERGENCY"]):
        c[i].metric(p, int((df["posture"] == p).sum()))

    left, right = st.columns([1.15, 1])
    with left:
        st.dataframe(df.style.apply(color_posture, subset=["posture"]),
                     use_container_width=True, hide_index=True)
    with right:
        postures = {bid: r["posture"] for bid, r in res.items()}
        m, have_geo = posture_map(postures)
        legend()
        st_folium(m, height=430, width=520, returned_objects=[])
        if not have_geo:
            st.caption("Outlet markers shown. Add cullowhee_subbasins.geojson "
                       "(from merge_subbasins.py) for true sub-basin polygons.")

with tab2:
    st.subheader("Posture grid — storms (rows) × antecedent (cols)")
    bsel = st.selectbox("Basin", list(tm.BASINS.keys()))
    ant = [("dry", 0.2), ("normal", 1.7), ("wet", 3.0)]
    grid = []
    for sname, sdepth in tm.DESIGN_DEPTH_IN.items():
        row = {"storm": sname}
        for aname, ap5 in ant:
            _, r = tm.run_case(sdepth, ap5, PRF=prf)
            row[aname] = r[bsel]["posture"]
        grid.append(row)
    gdf = pd.DataFrame(grid).set_index("storm")
    st.dataframe(gdf.style.apply(lambda s: color_posture(s), axis=0),
                 use_container_width=True)
    st.caption("Same basin, every storm × antecedent combination — shows how soil "
               "wetness shifts the posture for a given storm.")

with tab3:
    st.subheader("Replay a real daily-rain series")
    txt = st.text_area("Daily rainfall (inches, comma-separated; last value = peak day region)",
                       "0.3, 0.8, 1.2, 2.1, 1.6, 3.4, 9.8, 2.2")
    k = st.slider("API recession k", 0.80, 0.95, 0.90, 0.01)
    try:
        rains = [float(x) for x in txt.replace("\n", ",").split(",") if x.strip() != ""]
        api = tm.api_series(rains, k=k)
        peak_i = max(range(len(rains)), key=lambda i: rains[i])
        p5h = sum(rains[max(0, peak_i - 5):peak_i])
        arc_h, res_h = tm.run_case(rains[peak_i], p5h, PRF=prf)
        st.write(f"Peak day = **{rains[peak_i]}\"** · 5-day antecedent = **{p5h:.2f}\"** "
                 f"· API = **{api[peak_i]:.2f}** · **ARC-{arc_h}**")
        rows = [{"basin": bid, "stage (ft)": round(r["stage"], 2), "posture": r["posture"]}
                for bid, r in res_h.items()]
        hdf = pd.DataFrame(rows)
        cmap = st.columns([1, 1])
        with cmap[0]:
            st.dataframe(hdf.style.apply(color_posture, subset=["posture"]),
                         use_container_width=True, hide_index=True)
        with cmap[1]:
            postures = {bid: r["posture"] for bid, r in res_h.items()}
            mh, _ = posture_map(postures)
            legend()
            st_folium(mh, height=430, width=520, returned_objects=[])
    except ValueError:
        st.error("Enter comma-separated numbers, e.g. 0.3, 0.8, 1.2, ...")

st.divider()
st.caption("Tabletop model: triangular UH + rectangular Manning rating + HDc. "
           "Validates logic and the antecedent effect — not a replacement for the "
           "five-module engine's routing.")
