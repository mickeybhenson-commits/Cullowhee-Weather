"""
confluence_status.py - PROTOTYPE status for the Cullowhee Creek / Tuckasegee
River confluence (the CC-MOUTH-2340 area), where homes sit in the bottomland.

WHY THIS IS A SEPARATE NODE
  The confluence floods by a DIFFERENT mechanism than the rest of the system.
  The tributaries and the campus flood from their own rainfall-runoff (a
  discharge->stage problem, handled by flood_rating). The confluence floods by
  BACKWATER: when the Tuckasegee is high it dams up the creek mouth, so the
  controlling water surface there is set by the RIVER's stage, not by Cullowhee
  Creek's flow. That is why basins.py marks CC-MOUTH-2340 rating="none" - the
  creek's own rating is meaningless (~3x out of bank) at the mouth.

  Using creek flow alone would systematically UNDER-predict confluence flooding.
  So this node posts the WORSE of two mechanisms:
     confluence = max( creek's own §2 frequency posture ,
                       Tuckasegee backwater posture from the live gauge )

GAUGE (real, live - the one MEASURED node in the system)
  USGS 03508050 / NWS TKRN7 "Tuckasegee River at SR 1172 nr Cullowhee, NC"
    drainage area 147 sq mi,  gage datum 2111.45 ft NAVD88
    location 35.28778, -83.14389  -- ABOVE (upstream of) the Cullowhee confluence
  Because the gauge is upstream, use its DISCHARGE (nearly conserved to the
  confluence, small DA gain) and/or its NWS flood CATEGORIES; do NOT use its
  stage as the confluence elevation directly (the river surface drops between
  gauge and mouth). Being upstream also makes it a LEADING indicator: the river
  flood wave passes the gauge before reaching the mouth -> real warning lead on
  the backwater component, which the flashy creek reaches never have.

  NWS flood-category stages at TKRN7 (gage height, ft):
     action 13 | minor 16 | moderate 19 | major 22
  These are official, calibrated to impacts near the gauge. Mapping to the ladder:
     >= moderate (19) -> EMERGENCY ; >= minor (16) -> WARNING ; >= action (13) -> WATCH

STATUS / CAVEATS (prototype)
  - The gauge stage-category is used as a first-cut proxy for "the Tuckasegee is
    high enough to back up the mouth." It is defined AT THE GAUGE, ~3-4 km
    upstream; the exact confluence elevation needs a HEC-RAS profile with the
    gauge as the downstream boundary (the rigorous upgrade). Documented, not hidden.
  - Receptor (home) elevations are NOT yet wired: the project structures layer
    (V_E_STRUC) could not be parsed here and may not carry finished-floor
    elevations. `receptor_ffe_navd88` is exposed as an input; when the lowest
    home's FFE is surveyed, the confluence-specific EMERGENCY can be tied to it
    exactly (see receptor_check()).
  - The design case is COINCIDENT flooding (creek + river high together, as in
    Helene). max() captures "either mechanism floods"; a full joint-probability
    or 2D run is the design-grade treatment.

USAGE
  from confluence_status import confluence_status
  confluence_status(model_peak_q_cfs=5548, gage_ht_ft=5.21)   # creek peak + gauge stage
  confluence_status(qpf=10, wetness=0.25, gage_ht_ft=20.0)    # storm + river in moderate flood
  confluence_status(qpf=10, wetness=0.25, live=True)          # fetch the live gauge
"""

import json
import urllib.request

from basins import BASINS
from flood_rating import calibrate_peak, rp_from_q, category_from_rp
import cwm_model as cwm

# ---- gauge constants (sourced above) ----------------------------------------
GAUGE_SITE = "03508050"
GAUGE_NWS = "TKRN7"
GAUGE_NAME = "Tuckasegee River at SR 1172 nr Cullowhee, NC"
GAUGE_DA_SQMI = 147.0            # USGS (confirm vs main-stem expectation)
GAUGE_DATUM_NAVD88 = 2111.45     # ft, gage datum
GAUGE_ABOVE_CONFLUENCE = True

# NWS TKRN7 flood-category stages (gage height, ft) -> ladder
NWS_FLOOD_STAGES = {"action": 13.0, "minor": 16.0, "moderate": 19.0, "major": 22.0}

CONFLUENCE_BID = "CC-MOUTH-2340"
_RANK = {"NORMAL": 0, "WATCH": 1, "WARNING": 2, "EMERGENCY": 3}


# ---- creek side: Cullowhee Creek's own §2 frequency posture at the mouth -----
def creek_posture(model_peak_q_cfs):
    """Cullowhee Creek's OWN flood posture at the mouth, by discharge frequency
    (§2), independent of stage (the mouth has no valid stage rating)."""
    cq = calibrate_peak(model_peak_q_cfs, CONFLUENCE_BID)
    rp = rp_from_q(cq, BASINS[CONFLUENCE_BID]["reg_q"])
    return category_from_rp(rp), (round(rp) if rp is not None else None), round(cq)


# ---- river side: Tuckasegee backwater posture from the gauge -----------------
def backwater_posture(gage_ht_ft):
    """Map the Tuckasegee gauge stage to a backwater posture via the official
    NWS TKRN7 flood categories."""
    if gage_ht_ft is None:
        return "N/A", None
    s = NWS_FLOOD_STAGES
    if gage_ht_ft >= s["moderate"]:
        cat = "EMERGENCY"
    elif gage_ht_ft >= s["minor"]:
        cat = "WARNING"
    elif gage_ht_ft >= s["action"]:
        cat = "WATCH"
    else:
        cat = "NORMAL"
    wse = round(GAUGE_DATUM_NAVD88 + gage_ht_ft, 2)   # gauge WSE, NAVD88 (at the gauge)
    return cat, wse


def receptor_check(gage_ht_ft, receptor_ffe_navd88):
    """OPTIONAL, when a surveyed lowest-home finished-floor elevation is known.
    Compares the gauge water surface to the receptor. NOTE: this uses the gauge
    WSE as a stand-in for the confluence WSE; replace with a HEC-RAS-translated
    confluence elevation when available. Returns None until an FFE is supplied."""
    if receptor_ffe_navd88 is None or gage_ht_ft is None:
        return None
    gauge_wse = GAUGE_DATUM_NAVD88 + gage_ht_ft
    return {"gauge_wse_navd88": round(gauge_wse, 2),
            "receptor_ffe_navd88": receptor_ffe_navd88,
            "freeboard_ft": round(receptor_ffe_navd88 - gauge_wse, 2),
            "receptor_wet": gauge_wse >= receptor_ffe_navd88,
            "caveat": "gauge WSE used as confluence proxy; needs HEC-RAS translation"}


# ---- live gauge fetch (USGS Instantaneous Values service) --------------------
def fetch_gauge_live(timeout=15):
    """Fetch the latest discharge (00060) and gage height (00065) from USGS.
    Returns {'discharge_cfs','gage_ht_ft','timestamp'} or raises on failure.
    Deployment reads this; the self-test uses a snapshot and does not hit network."""
    url = ("https://waterservices.usgs.gov/nwis/iv/?format=json"
           f"&sites={GAUGE_SITE}&parameterCd=00060,00065")
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.load(r)
    out = {"discharge_cfs": None, "gage_ht_ft": None, "timestamp": None}
    for ts in data["value"]["timeSeries"]:
        code = ts["variable"]["variableCode"][0]["value"]
        val = ts["values"][0]["value"]
        if not val:
            continue
        v = float(val[-1]["value"])
        out["timestamp"] = val[-1]["dateTime"]
        if code == "00060":
            out["discharge_cfs"] = v
        elif code == "00065":
            out["gage_ht_ft"] = v
    return out


# ---- combined confluence status ---------------------------------------------
def confluence_status(model_peak_q_cfs=None, qpf=None, wetness=None,
                      gage_ht_ft=None, receptor_ffe_navd88=None, live=False):
    """Combined confluence posture = max(creek §2 posture, Tuckasegee backwater).

    Supply the creek side as either a model peak (model_peak_q_cfs) or a storm
    (qpf + wetness, run through cwm_model). Supply the river side as gage_ht_ft,
    or set live=True to fetch the gauge now.
    """
    # creek side
    if model_peak_q_cfs is None:
        if qpf is None:
            raise ValueError("provide model_peak_q_cfs or (qpf, wetness)")
        model_peak_q_cfs = cwm.assess(CONFLUENCE_BID, qpf, wetness if wetness is not None else 0.5)["qp_raw"]
    creek_cat, creek_rp, creek_cq = creek_posture(model_peak_q_cfs)

    # river side
    live_info = None
    if live and gage_ht_ft is None:
        live_info = fetch_gauge_live()
        gage_ht_ft = live_info["gage_ht_ft"]
    river_cat, gauge_wse = backwater_posture(gage_ht_ft)

    # combine (worse of the two; ignore N/A river when no gauge value)
    cats = [creek_cat] + ([river_cat] if river_cat != "N/A" else [])
    confluence = max(cats, key=lambda c: _RANK.get(c, -1))
    if confluence == "NORMAL":
        driver = "none"
    elif river_cat != "N/A" and _RANK.get(river_cat, -1) >= _RANK.get(creek_cat, -1):
        driver = "river-backwater"
    else:
        driver = "creek-runoff"

    return {
        "node": CONFLUENCE_BID, "confluence_posture": confluence, "driver": driver,
        "creek": {"posture": creek_cat, "return_period_yr": creek_rp, "calib_q_cfs": creek_cq},
        "river": {"posture": river_cat, "gage_ht_ft": gage_ht_ft, "gauge_wse_navd88": gauge_wse,
                  "gauge": f"USGS {GAUGE_SITE}/{GAUGE_NWS}"},
        "receptor": receptor_check(gage_ht_ft, receptor_ffe_navd88),
        "live": live_info,
    }


# ---- self-test (snapshot inputs; no network) --------------------------------
if __name__ == "__main__":
    print("=" * 92)
    print("CONFLUENCE STATUS PROTOTYPE - Cullowhee Creek x Tuckasegee River (CC-MOUTH-2340)")
    print(f"  gauge USGS {GAUGE_SITE}/{GAUGE_NWS}  DA {GAUGE_DA_SQMI} sq mi  datum {GAUGE_DATUM_NAVD88} NAVD88")
    print(f"  NWS flood stages (ft): {NWS_FLOOD_STAGES}")
    print("=" * 92)

    # Helene creek peak at the mouth (QPF=10, drought wetness 0.25)
    helene_peak = cwm.assess(CONFLUENCE_BID, 10, 0.25)["qp_raw"]

    scenarios = [
        ("Quiet day (snapshot 2026-07-19)",           helene_peak * 0.0 + cwm.assess(CONFLUENCE_BID, 1.0, 0.3)["qp_raw"], 5.21),
        ("Creek flood, river normal",                 helene_peak, 5.21),
        ("Creek normal, river MINOR flood (17 ft)",   cwm.assess(CONFLUENCE_BID, 1.0, 0.3)["qp_raw"], 17.0),
        ("COINCIDENT: Helene creek + river MODERATE", helene_peak, 20.0),
    ]
    hdr = f"{'scenario':<42}{'creek':<11}{'river':<11}{'CONFLUENCE':<12}driver"
    print(hdr); print("-" * len(hdr))
    for name, peak, gh in scenarios:
        r = confluence_status(model_peak_q_cfs=peak, gage_ht_ft=gh)
        print(f"{name:<42}{r['creek']['posture']:<11}{r['river']['posture']:<11}"
              f"{r['confluence_posture']:<12}{r['driver']}")
    print("-" * len(hdr))
    print("Interpretation: the node escalates when EITHER the creek's own flood frequency OR")
    print("the Tuckasegee backwater (via the live gauge) crosses a threshold - the worse governs.")
    print("Wire receptor_ffe_navd88 (lowest home, surveyed) to tie EMERGENCY to an actual floor.")
