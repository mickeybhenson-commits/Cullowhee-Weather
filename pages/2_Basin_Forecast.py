"""
pages/2_Basin_Forecast.py - live flood forecast for all eight Cullowhee Creek basins.

Renders forecast.py: live Open-Meteo QPF (rolling 24-h maximum) + 30-day API
antecedent wetness -> the calibrated engine (cwm_model -> flood_rating) -> a
posture, return period, confidence band, input-uncertainty ensemble and lead
time for every basin, plus the watershed roll-up.

Falls back to a scenario slider when the live fetch is unavailable, and says so
rather than silently showing modeled numbers as if they were live.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

import forecast as F
from basins import LEAD_REQ_MIN, routed_order

st.set_page_config(page_title="Basin Forecast", layout="wide")

SEV = {"NORMAL": "#1A7A52", "WATCH": "#C08A00", "WARNING": "#C2410C",
       "EMERGENCY": "#B42318", "N/A": "#8A97A4"}


def chip(p, big=False):
    c = SEV.get(p, "#8A97A4")
    label = "&mdash;" if p in (None, "N/A") else p.title()
    size = "1.0rem" if big else "0.8rem"
    pad = "3px 12px" if big else "1px 8px"
    return (f'<span style="background:{c}1a;color:{c};border:1px solid {c}55;'
            f'border-radius:10px;padding:{pad};font-size:{size};font-weight:600;'
            f'white-space:nowrap;">{label}</span>')


def num(v, fmt="{:,.0f}", dash="&mdash;"):
    return dash if v is None else fmt.format(v)


st.title("Cullowhee Creek — basin flood forecast")
st.caption("All eight basins, forecast-driven. Six of the eight have a time of "
           "concentration under the 120-minute operational lead requirement, so "
           "an observation-only product cannot warn them in time — that is the "
           "reason this view is QPF-driven rather than gage-driven.")


# ---------------------------------------------------------------------------
# Data: live, or an explicit scenario
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner="Fetching live rainfall…")
def live_forecast():
    return F.run()


mode = st.radio("Forcing", ["Live forecast", "Scenario"], horizontal=True,
                label_visibility="collapsed")

fc = None
if mode == "Live forecast":
    fc = live_forecast()
    if not fc.get("ok"):
        st.error(f"Live rainfall unavailable — {fc.get('error')}. "
                 "No forecast is shown rather than a fabricated one; "
                 "switch to **Scenario** to exercise the engine.")
        st.stop()
else:
    c1, c2 = st.columns(2)
    qpf = c1.slider("24-hr QPF (in)", 0.0, 14.0, 4.8, 0.1,
                    help="Design-storm depth applied to every basin. "
                         "~4.8 in is the 10-yr, ~7.5 in the 100-yr at the campus.")
    w = c2.slider("Antecedent wetness (0 = dry, 0.5 = normal, 1 = saturated)",
                  0.0, 1.0, 0.5, 0.05)
    fc = F.forecast_all({b: {"qpf_in": qpf, "wetness": w} for b in routed_order()})
    fc.update(ok=True, generated_utc="scenario (not live)",
              source=f"SCENARIO: QPF={qpf} in / 24 hr, wetness={w}")

ws = fc["watershed"]
rows = fc["basins"]

# ---------------------------------------------------------------------------
# Watershed headline
# ---------------------------------------------------------------------------
h1, h2, h3, h4 = st.columns([1.4, 1, 1, 1.2])
with h1:
    st.markdown("**Watershed posture**", help="Worst in-scope basin posture. "
                "The mouth is excluded: it floods by backwater from the "
                "Tuckasegee, which this engine does not rate.")
    st.markdown(chip(ws["posture"], big=True), unsafe_allow_html=True)
with h2:
    st.metric("Warning point (WCU campus)", ws["warning_point_posture"] or "—",
              help="The only reach with a field-validated stage rating "
                   "(7 / 9 / 11 ft; 11 ft = water in the road).")
with h3:
    st.metric("Campus stage", num(ws["warning_point_stage_ft"], "{:.1f} ft"))
with h4:
    st.metric("Max basin QPF", num(ws["max_qpf_in"], "{:.2f} in"))

st.caption(f"{fc.get('source')} · generated {fc.get('generated_utc')}")

if ws["basins_at_or_above_watch"]:
    st.warning("At or above WATCH: **"
               + "**, **".join(rows[b]["name"] for b in ws["basins_at_or_above_watch"])
               + "**")

# ---------------------------------------------------------------------------
# Per-basin table
# ---------------------------------------------------------------------------
COLS = ["Basin", "DA mi²", "QPF in", "Wet", "CN", "Calib Q cfs", "RP yr",
        "Posture", "Confidence", "Ensemble", "Stage ft", "Tc min", "Lead"]
_right = {"DA mi²", "QPF in", "Wet", "CN", "Calib Q cfs", "RP yr",
          "Stage ft", "Tc min"}

header = "".join(
    f"<th style='padding:6px 10px;{'text-align:right;' if c in _right else 'text-align:left;'}'>{c}</th>"
    for c in COLS)

body = ""
for bid in routed_order():
    r = rows.get(bid)
    if not r:
        continue
    is_wp = r["role"] == "warning_point"
    oos = r["role"] == "out_of_scope"
    bg = "background:#F1EFE8;" if is_wp else ("opacity:0.62;" if oos else "")

    band = r.get("rp_band_yr")
    conf = r.get("confidence") or "—"
    if band and band[0] is not None:
        conf = f"{conf}<br><span style='color:#8A97A4;font-size:0.74rem;'>" \
               f"{band[0]}–{band[1]} yr</span>"

    ens = "—"
    if r.get("ensemble_dist"):
        top = list(r["ensemble_dist"].items())[0]
        ens = ("<span style='color:#1A7A52;'>firm</span>" if r.get("ensemble_firm")
               else f"<span style='color:#C2410C;'>{top[0].title()} {top[1]:.0%}</span>")

    lead = ("<span style='color:#C2410C;'>limited</span>" if r["lead_limited"]
            else "<span style='color:#1A7A52;'>ok</span>")

    body += (
        f"<tr style='border-bottom:1px solid #E2E8ED;font-size:0.88rem;{bg}'>"
        f"<td style='padding:6px 10px;'>{r['name']}"
        + ("<br><span style='color:#8A97A4;font-size:0.72rem;'>warning point</span>"
           if is_wp else
           "<br><span style='color:#8A97A4;font-size:0.72rem;'>out of scope · backwater</span>"
           if oos else "")
        + f"</td>"
        f"<td style='padding:6px 10px;text-align:right;'>{r['da_sqmi']}</td>"
        f"<td style='padding:6px 10px;text-align:right;'>{r['qpf_in']:.2f}</td>"
        f"<td style='padding:6px 10px;text-align:right;'>{r['wetness']:.2f}</td>"
        f"<td style='padding:6px 10px;text-align:right;'>{r['cn']:.0f}</td>"
        f"<td style='padding:6px 10px;text-align:right;font-weight:600;'>{r['calib_q_cfs']:,}</td>"
        f"<td style='padding:6px 10px;text-align:right;'>{num(r['rp_best_yr'])}</td>"
        f"<td style='padding:6px 10px;'>{chip(r['posture'])}</td>"
        f"<td style='padding:6px 10px;font-size:0.78rem;'>{conf}</td>"
        f"<td style='padding:6px 10px;font-size:0.78rem;'>{ens}</td>"
        f"<td style='padding:6px 10px;text-align:right;'>{num(r['stage_total_ft'], '{:.2f}')}</td>"
        f"<td style='padding:6px 10px;text-align:right;'>{r['tc_min']}</td>"
        f"<td style='padding:6px 10px;font-size:0.78rem;'>{lead}</td></tr>")

st.markdown(
    f"<table style='width:100%;border-collapse:collapse;'>"
    f"<thead><tr style='text-align:left;border-bottom:2px solid #CBD3DA;"
    f"color:#5B6B7A;font-size:0.78rem;'>{header}</tr></thead>"
    f"<tbody>{body}</tbody></table>",
    unsafe_allow_html=True)

st.markdown("&nbsp;")

# ---------------------------------------------------------------------------
# Honest limits
# ---------------------------------------------------------------------------
st.warning(
    "**Shadow mode — not an official warning product.** NWS (WFO GSP) is the "
    "warning authority and NCEM FIMAN is the authoritative gage record. "
    + ws["qpf_bias_note"])

with st.expander("How to read this"):
    st.markdown(f"""
**Posture basis differs by reach, deliberately.**

* **WCU campus** posts on its field-**validated** stage ladder (7 / 9 / 11 ft,
  where 11 ft = water in the road). It is the only reach with a surveyed
  receptor and the only genuinely out-of-bank one.
* **The six tributary/mainstem reaches** post on **discharge return period**
  against the USGS StreamStats regression (WATCH ≥ 2-yr, WARNING ≥ 10-yr,
  EMERGENCY ≥ 100-yr; Cox and Long Branch drop WATCH to 1.5-yr because they
  are the flashiest). Their stage thresholds are still bankfull-referenced
  placeholders, and the rectangular Manning rating collapses above bankfull, so
  classifying by frequency sidesteps an invalid stage scale. This is the
  correction that fixed a demonstrated Helene under-warning.
* **The mouth** is out of scope: it floods by backwater from the Tuckasegee,
  which the creek's own rating cannot represent. Its creek-side frequency is
  shown for context only.

**Confidence** is the USGS 90% regression prediction interval expressed as a
posture range; **Ensemble** perturbs QPF ± 25% and wetness ± 0.15 on a 3×3 grid
and reports whether the call holds across the whole envelope.

**Lead** flags reaches whose time of concentration is under the
{LEAD_REQ_MIN}-minute operational requirement. Those reaches cannot be warned on
observation alone — by the time a gage shows the rise, the peak is arriving.
""")

with st.expander("Provenance"):
    st.markdown(f"""
* **Forcing** — {fc.get('source')}
* **Engine** — {fc.get('engine', 'cwm_model → flood_rating')}
* **Regression flows / prediction intervals** — USGS StreamStats regional
  regression, NC SIR 2023-5006
* **Campus stage rating** — TVA (1983) FPM-83/51 Tables 2/3
* Every basin record also carries `tc_model_min` (the Tc the unit hydrograph
  actually ran) alongside `tc_min` (the registry value driving the lead flag).
  They agree on six of eight reaches; **CC-MS-1100** and **CC-SPD-1830** differ,
  and both are reaches whose registry `tc_src` records a competing NRCS-wet
  estimate. The engine value is not silently reconciled, because the per-basin
  calibration anchors were fit at it and the Helene back-test validates against
  them.
""")
