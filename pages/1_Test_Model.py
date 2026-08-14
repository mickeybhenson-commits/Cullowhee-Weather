"""
pages/1_Test_Model.py - Cullowhee Creek engine view (corrected design-storm postures).

Design-storm postures through the calibrated engine: cwm_model for the rainfall ->
runoff -> unit-hydrograph chain, flood_rating.assess() for the operative posture.

2026-08-13: this page imported `test_model`, which moved to the private Cullowhee-Engine
repo in 9c720eb. wetness.py, outlook_engine.py and live_rainfall.py were all re-sourced
onto cwm_model at the time; this page was missed, so it had been raising
ModuleNotFoundError the moment anyone opened it. It is now on the in-repo engine.

Two deliberate changes made while re-sourcing:

  * The named design-storm menu is gone. It came from test_model.DESIGN_DEPTH_IN, which
    left with test_model, and inventing a depth table here would put unsourced numbers
    in front of an operator. Rainfall is a direct input now. The two depths this project
    does have on record are named in the control: 4.80 in and 7.50 in reproduce the
    10-yr and 100-yr StreamStats anchors every basin's `calib` was fitted through.

  * The ARC class (I/II/III) went with it. run_case returned an NRCS staircase class;
    this engine uses a CONTINUOUS CN from a wetness index in [0,1] and has no staircase
    to report. Reusing the old label for a different quantity is how a number quietly
    stops meaning what it says, so the page shows wetness and CN instead. Same call was
    made in live_rainfall.py on 2026-08-03.

Posture comes from flood_rating.assess() - the authoritative engine - NOT from the
stage ladder. For the seven non-campus reaches the ladder is a cross-check, not the call.

Requires basins.py, cwm_model.py, flood_rating.py, wetness.py at the repo root. The
sys.path line below lets this page import them from inside pages/.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import cwm_model as cwm
from basins import BASINS, routed_order
from flood_rating import assess as fr_assess
from wetness import resolve_wetness

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
depth = c1.slider("Rainfall (in, 24-hr SCS Type II)", 0.5, 15.0, 4.80, 0.05,
                  help="4.80 in reproduces the 10-yr StreamStats anchor that every "
                       "basin's calibration was fitted through; 7.50 in the 100-yr.")
ante_label = c2.radio("Antecedent soil moisture", list(ANTE.keys()), index=1, horizontal=True)
p5 = ANTE[ante_label]

wet, wet_src = resolve_wetness(p5_in=p5)

# Chain per basin: cwm_model for the physics, flood_rating.assess for the operative call.
# fr_assess takes the RAW unit-hydrograph peak and applies its own calibration, so qp_raw
# is what gets handed over - passing calib_q would apply the per-basin power law twice.
res = {}
for _bid in routed_order():
    _m = cwm.assess(_bid, depth, wet)
    _a = fr_assess(_m["qp_raw"], _bid)
    res[_bid] = {"Q": _m["runoff_in"], "qp": _m["qp_raw"], "calib_q": _a["calib_q"],
                 "stage": _m["stage"], "posture": _a["posture"],
                 "CN": _m["CN"], "rp": _a.get("rp_best")}

# --- campus headline ------------------------------------------------------
cw = res["CC-WCU-2260"]
m1, m2, m3 = st.columns(3)
m1.metric("WCU campus posture", cw["posture"], help="Receptor-validated 7/9/11 ladder")
m2.metric("Campus stage", "n/a" if cw["stage"] is None else f"{cw['stage']:.1f} ft")
m3.metric("Calibrated peak", f"{round(cw['calib_q']):,} cfs",
          help=f"raw model {round(cw['qp']):,} cfs (~2\u00d7 before correction)")
st.caption(f"{depth:.2f}\" / 24 hr \u00b7 wetness {wet:.2f} ({wet_src}) \u00b7 "
           f"CN {cw['CN']:.1f} at the campus \u00b7 posture from flood_rating.assess()")

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
    # Four states, not two. Five reaches gained SURVEYED LiDAR ladders on
    # 2026-08-03; labelling them 'placeholder' understates the evidence exactly
    # as badly as labelling them 'validated' would overstate it. The mouth has
    # no ladder at all, which is a third thing again.
    src = rec["thr_src"]
    if rec["thr_ft"] is None:
        thr = "<span style='color:#8A97A4;'>out of scope</span>"
    elif src.startswith("VALIDATED"):
        thr = "<span style='color:#1A7A52;'>validated</span>"
    elif src.startswith("SURVEYED"):
        thr = "<span style='color:#2F6FB5;'>surveyed</span>"
    else:
        thr = "<span style='color:#C2410C;'>placeholder</span>"
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
    "Threshold provenance is not uniform, and the column above says which is which. "
    "**Validated** \u2014 the campus only (11 ft = water in road, field-confirmed). "
    "**Surveyed** \u2014 five reaches (Tilley, Mainstem, Speedwell, Cox, Long Branch) on "
    "NC QL2 LiDAR pour-point pools, committed 2026-08-03: real terrain, but no receptor "
    "has been tied to them. **Placeholder** \u2014 Upper Cullowhee only, still "
    "bankfull\u00d7(1.0,1.5,2.0). **Out of scope** \u2014 the mouth, which is "
    "backwater-controlled by the Tuckasegee and has no creek stage ladder at all. "
    "Treat every non-campus posture as modeled, not a warning basis.")

with st.expander("What this is"):
    st.markdown(
        "- Runs `cwm_model.assess(basin, rain, wetness)` for the physics, then "
        "`flood_rating.assess()` for the operative posture.\n"
        "- **Model Q \u2192 Calib Q** is the per-basin regression bias correction (~1.9\u20132.8\u00d7), "
        "each basin its own factor.\n"
        "- Stage comes from the TVA rating (campus) or the in-bank rectangle (tributaries); "
        "the mouth returns no stage by design.\n"
        "- This is the design / forecast view \u2014 separate from the operational `flood_network` "
        "console, which is intentionally left untouched.")
