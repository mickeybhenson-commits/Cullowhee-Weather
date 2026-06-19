"""
test_app.py  —  Streamlit front-end for the Cullowhee flood-engine test model.

Run locally:        streamlit run test_app.py
On Streamlit Cloud: set this as the app's main file, OR drop it in a `pages/`
                    folder to appear alongside streamlit_app.py as a second page.

Requires test_model.py in the same folder (it imports from it — no logic is
duplicated here). Add `streamlit` and `pandas` to requirements.txt.
"""

import streamlit as st
import pandas as pd
import test_model as tm

st.set_page_config(page_title="Cullowhee flood — test model", layout="wide")

POSTURE_COLOR = {"NORMAL": "#1D9E75", "WATCH": "#EF9F27",
                 "WARNING": "#D85A30", "EMERGENCY": "#A32D2D"}

def color_posture(series):
    return [f"background-color:{POSTURE_COLOR.get(v,'')};color:white;font-weight:600"
            if v in POSTURE_COLOR else "" for v in series]

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
    st.dataframe(df.style.apply(color_posture, subset=["posture"]),
                 use_container_width=True, hide_index=True)

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
        st.dataframe(hdf.style.apply(color_posture, subset=["posture"]),
                     use_container_width=True, hide_index=True)
    except ValueError:
        st.error("Enter comma-separated numbers, e.g. 0.3, 0.8, 1.2, ...")

st.divider()
st.caption("Tabletop model: triangular UH + rectangular Manning rating + HDc. "
           "Validates logic and the antecedent effect — not a replacement for the "
           "five-module engine's routing.")
