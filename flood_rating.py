"""
flood_rating.py - one rating/posture engine for every Cullowhee Creek node.

Reads basins.py. Replaces the per-basin-file approach: there is no campus-specific
or Long-Branch-specific module - the basin record selects the behavior.

SOURCE LADDER (mirrors sources.py: MEASURED > GOV_ESTIMATE > MODELED)
  DISCHARGE: live model peak -> calibrate_peak() bias-corrects it onto the
             regression scale (GOV_ESTIMATE) per basin. A learned/sensor rating
             would override later (MEASURED).
  RATING   : rec["rating"] picks the path -
               "tva"         -> surveyed stage-discharge (campus only)   GOV_ESTIMATE
               "rectangular" -> Bieger-bankfull section, valid in-bank   MODELED
               "none"        -> out of scope (mouth)
  POSTURE  : depth vs rec["thr_ft"]. Campus thresholds are receptor-validated;
             the other seven are PLACEHOLDERS (see basins.py thr_src) - this is
             the remaining gap, pending surveyed top-of-bank / receptors.

USAGE
  from flood_rating import assess
  assess(model_peak_q_cfs, "CC-WCU-2260")   # full chain -> dict

INTEGRATION (test_model.py): per basin, replace the qp -> stage block with
  from flood_rating import calibrate_peak, depth_from_q, posture
  stage   = depth_from_q(calibrate_peak(qp, bid), bid)
  status  = posture(stage, bid)
The synthetic-rectangle code stays for the 6 tributaries (now fed corrected Q,
so it operates in-bank where it is valid); the campus path becomes the TVA rating.
"""

import math
from basins import BASINS, routed_order


# --- discharge: per-basin bias correction onto the regression scale -----------
def calibrate_peak(model_q_cfs, bid):
    """Map this model's TR-55/UH peak onto the basin's regression frequency.
    Q_reg = a * Q_model^b, fit per basin to its 10-yr & 100-yr anchors. Exact at
    those two points; absorbs the 1.9-2.8x methodology bias (peaked design storm
    vs USGS regression)."""
    a, b = BASINS[bid]["calib"]
    return a * model_q_cfs ** b if model_q_cfs > 0 else 0.0


# --- rating: depth (ft above bed) from discharge ------------------------------
def _rect_q(depth, sec):
    w, n, s = sec["w"], sec["n"], sec["s"]
    A = w * depth
    P = w + 2.0 * depth
    R = A / P if P > 0 else 0.0
    return (1.49 / n) * A * R ** (2.0 / 3.0) * s ** 0.5

def _rect_depth(q, sec, dmax=30.0):
    lo, hi = 0.0, dmax
    if _rect_q(hi, sec) < q:
        return hi
    for _ in range(60):
        m = 0.5 * (lo + hi)
        if _rect_q(m, sec) < q:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)

def _tva_rating(rec):
    bed = rec["bed_ft"]
    pts = [(q, wse - bed) for q, wse in rec["tva_wse"].values()]
    lx = [math.log(d) for _, d in pts]
    ly = [math.log(q) for q, _ in pts]
    n = len(lx)
    mx, my = sum(lx) / n, sum(ly) / n
    B = sum((x - mx) * (y - my) for x, y in zip(lx, ly)) / sum((x - mx) ** 2 for x in lx)
    C = math.exp(my - B * mx)
    return C, B   # Q = C * depth^B

def depth_from_q(q_cfs, bid):
    rec = BASINS[bid]
    if q_cfs is None or q_cfs <= 0:
        return 0.0
    if rec["rating"] == "tva":
        C, B = _tva_rating(rec)
        return (q_cfs / C) ** (1.0 / B)
    if rec["rating"] == "rectangular":
        return _rect_depth(q_cfs, rec["section"])
    return None   # out of scope


# --- posture ------------------------------------------------------------------
def posture(depth_ft, bid):
    t = BASINS[bid]["thr_ft"]
    if t is None or depth_ft is None:
        return "N/A"
    watch, warn, emerg = t
    if depth_ft >= emerg:
        return "EMERGENCY"
    if depth_ft >= warn:
        return "WARNING"
    if depth_ft >= watch:
        return "WATCH"
    return "NORMAL"


def assess(model_peak_q_cfs, bid):
    cq = calibrate_peak(model_peak_q_cfs, bid)
    d = depth_from_q(cq, bid)
    return {"basin": bid, "model_q": round(model_peak_q_cfs), "calib_q": round(cq),
            "depth_ft": round(d, 1) if d is not None else None,
            "posture": posture(d, bid), "rating": BASINS[bid]["rating"],
            "thr_validated": BASINS[bid]["thr_src"].startswith("VALIDATED")}


# --- self-test: corrected posture for all eight, vs the old synthetic model ---
if __name__ == "__main__":
    # old synthetic-model depths (10-yr design storm) for contrast
    OLD = {"CC-UP-503":4.72,"CC-MS-1100":5.54,"CC-TIL-705":4.66,"CC-SPD-1830":7.13,
           "CC-COX-097":2.65,"CC-LB-171":3.34,"CC-WCU-2260":16.22,"CC-MOUTH-2340":13.69}
    print("=" * 88)
    print("ENGINE SELF-TEST - 10-yr design storm, all 8 nodes")
    print("  model peak (raw TR-55) -> per-basin calibration -> rating -> posture")
    print("=" * 88)
    hdr = f"{'basin':14s}{'model Q':>8}{'calib Q':>8}{'rating':>12}{'depth':>7}{'OLD depth':>10}  {'posture':<10}thr"
    print(hdr); print("-" * len(hdr))
    for bid in routed_order():
        mq = BASINS[bid]["calib_anchors"][0][0]   # the model 10-yr peak
        a = assess(mq, bid)
        old = f"{OLD[bid]:.1f}" if bid in OLD else "--"
        dep = f"{a['depth_ft']:.1f}" if a['depth_ft'] is not None else "--"
        tag = "validated" if a["thr_validated"] else "PLACEHOLDER"
        print(f"{bid:14s}{a['model_q']:8d}{a['calib_q']:8d}{a['rating']:>12}"
              f"{dep:>7}{old:>10}  {a['posture']:<10}{tag}")
    print("-" * len(hdr))
    print("Discharge halved-ish across the board (calibrated to regression); the 6 tributaries")
    print("now rate IN-BANK on the rectangle, only the campus uses TVA. Postures are sound on the")
    print("campus (receptor-validated) and provisional on the rest (placeholder thresholds).")
