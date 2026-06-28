"""
pages/1_Test_Model.py - Cullowhee Creek engine view (corrected design-storm postures).

Renders test_model.run_case() through the calibrated engine (basins.py + flood_rating.py):
per-basin regression bias correction, TVA rating at the campus, in-bank rectangle for the
tributaries. Replaces the old synthetic path that over-predicted ~2x.

Requires test_model.py, basins.py, flood_rating.py at the repo root. The sys.path line
below lets this page import them from inside pages/.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import test_model as tm
from basins import BASINS, routed_order

st.set_page_config(page_title="Engine - Test Model", layout="wide")

SEV = {"NORMAL": "#1A7A52", "WATCH": "#C08A00", "WARNING": "#C2410C",
       "EMERGENCY": "#B42318", "N/A": "#8A97A4"}
ANTE = {'Dry (P5 0.2")': 0.2, 'Normal (P5 1.7")': 1.7, 'Wet (P5 3.0")': 3.0}


def fmt_stage(s):
    return "&mdash;" if s is None else f"{s:.1f}&nbsp;ft"


def chip(p):
    c = SEV.get(p, "#8A97A4")
    label = "&mdash;" if p == "N/A" else p.title()
    return (f'<span style="background:{c}1a;color:{c};border:1px solid {c}55;'
            f'border-radius:10px;padding:1px 8px;font-size:0.8rem;font-weight:600;'
            f'white-space:nowrap;">{label}</span>')


st.title("Cullowhee Creek \u2014 engine test model")
st.caption("Design-storm postures through the calibrated engine: per-basin regression "
           "calibration, TVA rating at the campus, in-bank rectangle elsewhere. "
           "Corrected from the old synthetic path that over-predicted ~2\u00d7.")

c1, c2 = st.columns(2)
storm = c1.selectbox("Design storm (24-hr SCS Type II)", list(tm.DESIGN_DEPTH_IN.keys()), index=1)
ante_label = c2.radio("Antecedent soil moisture", list(ANTE.keys()), index=1, horizontal=True)
depth = tm.DESIGN_DEPTH_IN[storm]
p5 = ANTE[ante_label]

arc, res = tm.run_case(depth, p5)
arc_tag = {1: "ARC-I (dry)", 2: "ARC-II (normal)", 3: "ARC-III (wet)"}[arc]

# --- campus headline ------------------------------------------------------
cw = res["CC-WCU-2260"]
m1, m2, m3 = st.columns(3)
m1.metric("WCU campus posture", cw["posture"], help="Receptor-validated 7/9/11 ladder")
m2.metric("Campus stage", "n/a" if cw["stage"] is None else f"{cw['stage']:.1f} ft")
m3.metric("Calibrated peak", f"{round(cw['calib_q']):,} cfs",
          help=f"raw model {round(cw['qp']):,} cfs (~2\u00d7 before correction)")
st.caption(f"{storm} storm \u00b7 {depth}\" / 24 hr \u00b7 {arc_tag}")

# --- table (HTML, None-safe, colored postures) ----------------------------
header = ("<tr style='text-align:left;border-bottom:2px solid #CBD3DA;color:#5B6B7A;"
          "font-size:0.78rem;'>"
          "<th style='padding:6px 10px;'>Reach</th>"
          "<th style='padding:6px 10px;text-align:right;'>DA mi\u00b2</th>"
          "<th style='padding:6px 10px;text-align:right;'>Runoff in</th>"
          "<th style='padding:6px 10px;text-align:right;'>Model Q</th>"
          "<th style='padding:6px 10px;text-align:right;'>Calib Q</th>"
          "<th style='padding:6px 10px;text-align:right;'>Stage</th>"
          "<th style='padding:6px 10px;'>Rating</th>"
          "<th style='padding:6px 10px;'>Posture</th>"
          "<th style='padding:6px 10px;'>Threshold</th></tr>")

body = ""
for bid in routed_order():
    r = res[bid]
    rec = BASINS[bid]
    me = bid == "CC-WCU-2260"
    bg = "background:#F1EFE8;" if me else ""
    thr_ok = rec["thr_src"].startswith("VALIDATED")
    thr = ("<span style='color:#1A7A52;'>validated</span>" if thr_ok
           else "<span style='color:#C2410C;'>placeholder</span>")
    body += (
        f"<tr style='border-bottom:1px solid #E2E8ED;font-size:0.88rem;{bg}'>"
        f"<td style='padding:6px 10px;'>{rec['name']}</td>"
        f"<td style='padding:6px 10px;text-align:right;'>{rec['da_sqmi']}</td>"
        f"<td style='padding:6px 10px;text-align:right;'>{r['Q']:.2f}</td>"
        f"<td style='padding:6px 10px;text-align:right;color:#8A97A4;'>{round(r['qp']):,}</td>"
        f"<td style='padding:6px 10px;text-align:right;font-weight:600;'>{round(r['calib_q']):,}</td>"
        f"<td style='padding:6px 10px;text-align:right;'>{fmt_stage(r['stage'])}</td>"
        f"<td style='padding:6px 10px;font-size:0.82rem;'>{rec['rating']}</td>"
        f"<td style='padding:6px 10px;'>{chip(r['posture'])}</td>"
        f"<td style='padding:6px 10px;font-size:0.82rem;'>{thr}</td></tr>")

st.markdown(
    f"<table style='width:100%;border-collapse:collapse;'>"
    f"<thead>{header}</thead><tbody>{body}</tbody></table>",
    unsafe_allow_html=True)

st.markdown("&nbsp;")
st.warning(
    "Only the campus threshold is receptor-validated (11 ft = water in road). The other "
    "seven reaches show calibrated discharge and physical stage, but their WATCH/WARNING/"
    "EMERGENCY thresholds are placeholders (bankfull-referenced) until surveyed receptors "
    "exist \u2014 treat those postures as modeled, not a warning basis. The mouth is out of "
    "scope (no rating, no stage).")

with st.expander("What this is"):
    st.markdown(
        "- Runs `test_model.run_case(storm, antecedent)` through `basins.py` + `flood_rating.py`.\n"
        "- **Model Q \u2192 Calib Q** is the per-basin regression bias correction (~1.9\u20132.8\u00d7), "
        "each basin its own factor.\n"
        "- Stage comes from the TVA rating (campus) or the in-bank rectangle (tributaries); "
        "the mouth returns no stage by design.\n"
        "- This is the design / forecast view \u2014 separate from the operational `flood_network` "
        "console, which is intentionally left untouched.")
