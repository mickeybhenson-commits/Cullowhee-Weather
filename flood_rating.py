"""
flood_rating.py - one rating/posture engine for every Cullowhee Creek node.

Reads basins.py. Replaces the per-basin-file approach: there is no campus-specific
or Long-Branch-specific module - the basin record selects the behavior.

=============================================================================
2026-07 IMPROVEMENT SET (reviewed & approved by Dr. M. B. Henson, 2026-07-15)
  §2  Non-campus reaches now post by DISCHARGE RETURN-PERIOD, not by the
      placeholder out-of-bank stage rating. The rectangular Manning rating
      collapses above bankfull (returns ~4-5 ft where the FIS 100-yr is ~10.8 ft
      above bed), so its stage cannot be trusted out of bank, and the seven
      non-campus thresholds rode that broken scale as bankfull x(1.0,1.5,2.0)
      placeholders. Classifying by USGS regression frequency sidesteps the
      invalid stage entirely and is strictly safer (it corrects a demonstrated
      Helene under-warning; see backtest_helene.py).
        WATCH     >= 2-yr    (channel-full / action)
        WARNING   >= 10-yr   (minor-moderate flooding)
        EMERGENCY >= 100-yr  (base flood / receptors threatened)
      Flashiest lead-limited reaches (Cox, Long Branch) drop WATCH to 1.5-yr.
  §3  Every non-campus posture carries a CONFIDENCE BAND from the USGS 90%
      regression prediction interval (basins.py reg_pi) - a single number is
      replaced by best-estimate + [low, high] return period. See pi_band().
  CAMPUS (CC-WCU-2260) is unchanged: it keeps its field-VALIDATED 7/9/11 ft
  TVA stage rating, which is authoritative and in-bank-valid there.
=============================================================================

SOURCE LADDER (mirrors sources.py: MEASURED > GOV_ESTIMATE > MODELED)
  DISCHARGE: live model peak -> calibrate_peak() bias-corrects it onto the
             regression scale (GOV_ESTIMATE) per basin. A learned/sensor rating
             would override later (MEASURED).
  POSTURE  : rec["rating"] picks the path -
               "tva"         -> surveyed stage-discharge + validated thresholds
                                (campus only)                            GOV_ESTIMATE
               "rectangular" -> discharge return-period vs reg_q          GOV_ESTIMATE
                                (stage retained as cross-check only)
               "none"        -> out of scope (mouth)

USAGE
  from flood_rating import assess
  assess(model_peak_q_cfs, "CC-WCU-2260")   # full chain -> dict

INTEGRATION (test_model.py): per basin, replace the qp -> stage block with
  from flood_rating import assess
  result = assess(qp, bid)          # result["posture"] is the operative call
The frequency path needs no surveyed thresholds, so it is live now for all
seven non-campus reaches; surveyed stage thresholds (bfe_to_thresholds.py) will
later add a second, independent stage check where the shapefiles land.
"""

import math
from basins import BASINS, routed_order, LEAD_REQ_MIN

# AEP key -> return period (years). Ordered dense->rare.
AEP_RP = [(0.50, 2), (0.20, 5), (0.10, 10), (0.04, 25),
          (0.02, 50), (0.01, 100), (0.005, 200), (0.002, 500)]

# Category cutoffs by return period (years). Default per §2.
CAT_CUTOFFS = {"EMERGENCY": 100, "WARNING": 10, "WATCH": 2}
# Flashiest lead-limited reaches: drop WATCH to 1.5-yr to offset short Tc (§2 refinement).
WATCH_1_5YR = {"CC-COX-097", "CC-LB-171"}
_ORDER = ["NORMAL", "WATCH", "WARNING", "EMERGENCY"]


# --- discharge: per-basin bias correction onto the regression scale -----------
def calibrate_peak(model_q_cfs, bid):
    """Map this model's TR-55/UH peak onto the basin's regression frequency.
    Q_reg = a * Q_model^b, fit per basin to its 10-yr & 100-yr anchors. Exact at
    those two points; absorbs the 1.9-2.8x methodology bias (peaked design storm
    vs USGS regression)."""
    a, b = BASINS[bid]["calib"]
    return a * model_q_cfs ** b if model_q_cfs > 0 else 0.0


# --- §2 discharge return-period classification --------------------------------
def rp_from_q(q_cfs, reg_q):
    """Return period (years) implied by a discharge on the regression curve.
    Log-linear interpolation in flow. Below the 2-yr the RP scales down toward 0;
    above the 500-yr it is capped at 500 (regression is not extrapolated)."""
    pts = sorted(((reg_q[a], rp) for a, rp in AEP_RP), key=lambda t: t[0])
    if q_cfs <= pts[0][0]:
        return pts[0][1] * q_cfs / pts[0][0]          # below 2-yr
    if q_cfs >= pts[-1][0]:
        return pts[-1][1]                              # cap at 500-yr
    for i in range(len(pts) - 1):
        (q0, r0), (q1, r1) = pts[i], pts[i + 1]
        if q0 <= q_cfs <= q1:
            f = (math.log(q_cfs) - math.log(q0)) / (math.log(q1) - math.log(q0))
            return r0 + f * (r1 - r0)
    return None


def category_from_rp(T, bid=None):
    """Map a return period (years) to a posture category (§2)."""
    if T is None:
        return "N/A"
    watch = 1.5 if bid in WATCH_1_5YR else CAT_CUTOFFS["WATCH"]
    if T >= CAT_CUTOFFS["EMERGENCY"]:
        return "EMERGENCY"
    if T >= CAT_CUTOFFS["WARNING"]:
        return "WARNING"
    if T >= watch:
        return "WATCH"
    return "NORMAL"


# --- §3 USGS prediction-interval confidence band ------------------------------
def pi_band(q_cfs, bid):
    """Best-estimate return period plus the RP inferred at the low/high edge of
    the USGS 90% regression prediction interval (basins.py reg_pi), i.e. the
    regression uncertainty on an ungauged flow.

    reg_pi anchors the PI at the 10-yr and 100-yr as (low_flow, high_flow). The
    low/high multipliers on the median flow are log-interpolated across RP. A
    FIXED flow q, read against the PI-LOWER curve (lower flow for a given RP),
    implies a RARER event (higher RP); against the PI-UPPER curve, a commoner
    one (lower RP)."""
    rec = BASINS[bid]
    rq = rec["reg_q"]
    pi = rec.get("reg_pi")
    best = rp_from_q(q_cfs, rq)
    if not pi or best is None:
        return best, None, None
    lo10, hi10 = pi[0.10]
    lo01, hi01 = pi[0.01]
    rlo10, rhi10 = lo10 / rq[0.10], hi10 / rq[0.10]
    rlo01, rhi01 = lo01 / rq[0.01], hi01 / rq[0.01]

    def scale(which, T):
        if T <= 10:
            return rlo10 if which == "lo" else rhi10
        f = (math.log(max(T, 10)) - math.log(10)) / (math.log(100) - math.log(10))
        f = min(max(f, 0.0), 1.0)
        a = rlo10 if which == "lo" else rhi10
        c = rlo01 if which == "lo" else rhi01
        return a + f * (c - a)

    rp_hi = rp_from_q(q_cfs / scale("lo", best), rq)   # PI-lower flows -> our q rarer
    rp_lo = rp_from_q(q_cfs / scale("hi", best), rq)   # PI-upper flows -> our q commoner
    return best, rp_lo, rp_hi


# --- rating: depth (ft above bed) from discharge (campus + cross-check) --------
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


def posture_stage(depth_ft, bid):
    """LEGACY stage-vs-threshold posture. Authoritative ONLY for the campus
    (validated 7/9/11 ft). For the seven non-campus reaches this rides the
    placeholder out-of-bank stage and is retained as a CROSS-CHECK, not the call
    (see §2). Kept so the campus path and audits still work."""
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


# --- unified assessment -------------------------------------------------------
def assess(model_peak_q_cfs, bid):
    """Full chain -> posture dict. Frequency classification is the operative call
    for the seven non-campus reaches (§2); the campus keeps its validated TVA
    stage. Every non-campus posture carries a USGS PI confidence band (§3).

    Keys:
      posture       operative call (what operators act on)
      basis         how `posture` was derived
      calib_q       regression-scale discharge (cfs)
      rp_best       best-estimate return period (years) or None
      rp_band       (low, high) return period from the USGS 90% PI, or None
      confidence    "firm" or "LOW_CAT-HIGH_CAT" band
      depth_ft      stage cross-check (ft above bed) where a rating exists
      stage_posture legacy stage-based posture (cross-check; campus = authoritative)
      thr_validated True only where thr_ft is field-validated (campus)
    """
    rec = BASINS[bid]
    cq = calibrate_peak(model_peak_q_cfs, bid)
    depth = depth_from_q(cq, bid)
    stage_post = posture_stage(depth, bid)
    validated = rec["thr_src"].startswith("VALIDATED")
    out = {
        "basin": bid, "model_q": round(model_peak_q_cfs), "calib_q": round(cq),
        "rating": rec["rating"],
        "depth_ft": round(depth, 1) if depth is not None else None,
        "stage_posture": stage_post, "thr_validated": validated,
    }

    if rec["rating"] == "none":                       # mouth = Cullowhee/Tuckasegee confluence
        # The mouth floods by BACKWATER from the Tuckasegee, which the creek's own
        # rating cannot represent (why rating="none"). This engine can compute the
        # CREEK half of the confluence posture (its own §2 discharge frequency);
        # the operational status combines that with the live Tuckasegee gauge in
        # confluence_status.confluence_status(). No longer "N/A" — it gives a status.
        best = rp_from_q(cq, rec["reg_q"])
        out.update(posture=category_from_rp(best, bid),
                   basis="creek frequency at confluence (add Tuckasegee backwater via "
                         "confluence_status + live USGS 03508050/TKRN7 for operational posture)",
                   rp_best=round(best) if best is not None else None,
                   rp_band=None,
                   confidence="creek-only (backwater not included here)")
        return out

    best = rp_from_q(cq, rec["reg_q"])
    out["rp_best"] = round(best) if best is not None else None

    if rec["rating"] == "tva":                        # campus: validated stage
        out.update(posture=stage_post,
                   basis="validated stage (TVA 7/9/11 ft)",
                   rp_band=None, confidence="validated")
        return out

    # non-campus: §2 frequency classification + §3 PI band
    b, rlo, rhi = pi_band(cq, bid)
    cat = category_from_rp(b, bid)
    clo = category_from_rp(rlo, bid)
    chi = category_from_rp(rhi, bid)
    band = sorted({clo, cat, chi}, key=_ORDER.index)
    confidence = "firm" if clo == cat == chi else f"{band[0]}-{band[-1]}"
    out.update(posture=cat, basis="discharge frequency (USGS regression)",
               rp_band=(round(rlo) if rlo is not None else None,
                        round(rhi) if rhi is not None else None),
               confidence=confidence)
    return out


# --- self-test: corrected posture for all eight, Helene + 10-yr design storm ---
if __name__ == "__main__":
    print("=" * 92)
    print("ENGINE SELF-TEST - frequency classification (§2) + PI band (§3)")
    print("  model peak -> per-basin calibration -> return-period -> posture (+ PI band)")
    print("=" * 92)
    # model 10-yr peaks (calib_anchors[0][0]) as a mild-storm reference row
    hdr = (f"{'basin':14s}{'calib Q':>9}{'rating':>12}{'RP yr':>7}"
           f"  {'posture':<10}{'confidence band':<20}stage x-chk")
    print(hdr); print("-" * len(hdr))
    for bid in routed_order():
        mq = BASINS[bid]["calib_anchors"][0][0]      # model 10-yr peak
        a = assess(mq, bid)
        rp = a["rp_best"] if a["rp_best"] is not None else "--"
        dep = f"{a['depth_ft']:.1f}" if a["depth_ft"] is not None else "--"
        print(f"{bid:14s}{a['calib_q']:9d}{a['rating']:>12}{str(rp):>7}"
              f"  {a['posture']:<10}{str(a['confidence']):<20}{dep}")
    print("-" * len(hdr))
    print("Non-campus reaches classify by discharge frequency; campus keeps validated stage;")
    print("mouth is out of scope. Run backtest_helene.py for the validation event.")
