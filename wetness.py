"""
wetness.py - antecedent wetness + baseflow engine for the Cullowhee Creek model.
================================================================================
AUTHORITATIVE implementation. The JS engine embedded in
Cullowhee_Creek_live_status.html is a PORT of this module - keep them in sync.

WHAT THIS REPLACES (and why)
  The original antecedent path was the NRCS 5-day ARC staircase:
    arc_class(p5) -> {I, II, III} -> cn_adjust()
  Three known weaknesses, all fixed here:
    1. 5-day unweighted memory (day 6 rain vanishes; day 5 counts fully)
       -> 30-day decayed Antecedent Precipitation Index (API), k = 0.90/day.
    2. 3-step CN staircase (discontinuous jumps at the breakpoints)
       -> continuous CN interpolated between CN(I) and CN(III) via a
          wetness index w in [0, 1], with w = 0.5 anchored at CN(II).
    3. Growing-season thresholds applied year-round (dormant-season
       breakpoints existed in test_model.py but were never invoked)
       -> season selected from the calendar month (Apr-Oct = growing).

WETNESS SOURCE LADDER (mirrors sources.py MEASURED > GOV_ESTIMATE > MODELED)
  1. TEROS soil moisture (MEASURED)          - future; not wired yet
  2. Modeled soil-moisture PERCENTILE        - preferred pre-sensor source.
     Absolute modeled VWC carries terrain bias; ranking today's value against
     the same grid cell's own recent history largely cancels it. w = percentile
     (median conditions = ARC-II by construction).
  3. 30-day API from modeled/observed rain   - fallback when soil feed drops.
  4. Legacy 5-day stepped ARC                - retained in test_model.py only.

BASEFLOW
  The rating chain produces STORM depth above the bed; thresholds are TOTAL
  stage. D0_FT adds a per-basin baseflow depth so posture compares like with
  like. Omitting it under-calls posture (non-conservative) - see 2026-07-10
  review. Campus d0 is OBSERVED; tributaries are MODELED through their own
  rating from a provisional Southern Blue Ridge baseflow yield and MUST be
  field-verified. Adding d0 only ever moves posture earlier (conservative).

REQUIRES basins.py and flood_rating.py in the same directory.
"""

import math
from datetime import date

from basins import BASINS
from flood_rating import depth_from_q, posture

# ----------------------------------------------------------------------------
# 1. CONTINUOUS CURVE NUMBER
# ----------------------------------------------------------------------------

def cn_bounds(cn2):
    """(CN_I dry, CN_II, CN_III wet) via the standard NRCS relations."""
    cn1 = cn2 / (2.281 - 0.01281 * cn2)
    cn3 = cn2 / (0.427 + 0.00573 * cn2)
    return cn1, cn2, cn3


def cn_from_wetness(cn2, w):
    """Continuous CN from wetness index w in [0,1].
    w=0 -> CN(I), w=0.5 -> CN(II), w=1 -> CN(III); piecewise linear.
    Replaces the 3-step cn_adjust() staircase for the live/forecast path."""
    w = max(0.0, min(1.0, w))
    cn1, cnm, cn3 = cn_bounds(cn2)
    if w < 0.5:
        return cn1 + (cnm - cn1) * (w / 0.5)
    return cnm + (cn3 - cnm) * ((w - 0.5) / 0.5)


# ----------------------------------------------------------------------------
# 2. 30-DAY DECAYED API  (rain-driven wetness; fallback source)
# ----------------------------------------------------------------------------

API_K = 0.90          # daily decay; ~10-day e-folding memory
API_DAYS = 30
# Steady-rain equivalence between the 30-day API and the 5-day sum the NRCS
# breakpoints were defined on:  sum_{i=0..29} k^i / 5  =  1.9153 at k=0.90.
API_5DAY_EQUIV = (1.0 - API_K ** API_DAYS) / (1.0 - API_K) / 5.0   # = 1.9153


def api_from_daily(daily_rain_in, k=API_K):
    """Decayed API over a daily series (oldest first, most recent last).
    API_t = k * API_{t-1} + P_t. Feed the last API_DAYS days."""
    api = 0.0
    for p in daily_rain_in[-API_DAYS:]:
        api = k * api + (p or 0.0)
    return api


def is_growing_season(month=None):
    """NRCS growing vs dormant season. Apr-Oct growing for the Southern
    Blue Ridge (WNC frost climatology); PROVISIONAL - refine per Coweeta."""
    m = month if month is not None else date.today().month
    return 4 <= m <= 10


def wetness_from_api(api_in, month=None):
    """Wetness index from the 30-day API, season-aware.
    NRCS 5-day breakpoints (1.4/2.1 growing, 0.5/1.1 dormant) are rescaled by
    API_5DAY_EQUIV so steady-rain behavior matches the classic table exactly;
    the interpolation between/below them is what makes CN continuous."""
    g = is_growing_season(month)
    lo = (1.4 if g else 0.5) * API_5DAY_EQUIV
    hi = (2.1 if g else 1.1) * API_5DAY_EQUIV
    if api_in <= 0.0:
        return 0.0
    if api_in < lo:
        return 0.5 * api_in / lo
    if api_in <= hi:
        return 0.5 + 0.5 * (api_in - lo) / (hi - lo)
    return 1.0


# ----------------------------------------------------------------------------
# 3. SOIL-MOISTURE PERCENTILE  (preferred pre-sensor wetness source)
# ----------------------------------------------------------------------------

def soil_percentile(current_vwc, history_vwc):
    """Empirical percentile of the current depth-weighted VWC against the SAME
    grid cell's recent history (>= ~30 days recommended). Using the percentile
    rather than the absolute value cancels most of the model's terrain bias.
    Returns None if history is too thin to rank against (< 48 samples)."""
    hist = [v for v in history_vwc if v is not None]
    if current_vwc is None or len(hist) < 48:
        return None
    below = sum(1 for v in hist if v <= current_vwc)
    return below / len(hist)


def wetness_from_soil_percentile(pct):
    """w = percentile, clamped. Median cell conditions = ARC-II by design.
    Supersedes the PROVISIONAL absolute-VWC bands (arc_from_soil)."""
    return max(0.0, min(1.0, pct))


def resolve_wetness(soil_pct=None, api_in=None, p5_in=None, month=None):
    """Source ladder: soil percentile > 30-day API > legacy 5-day sum
    (converted to API scale). Returns (wetness, source_tag)."""
    if soil_pct is not None:
        return wetness_from_soil_percentile(soil_pct), "soil_percentile"
    if api_in is not None:
        return wetness_from_api(api_in, month), "api30"
    if p5_in is not None:
        return wetness_from_api(p5_in * API_5DAY_EQUIV, month), "p5_legacy"
    return 0.5, "default_ARC_II"


# ----------------------------------------------------------------------------
# 4. BASEFLOW  (added as DISCHARGE before the rating; observed stage floor)
# ----------------------------------------------------------------------------
# Both ratings (TVA power law, rectangular Manning) map TOTAL discharge to
# TOTAL depth. Baseflow therefore enters as Qb (cfs) added to the calibrated
# storm peak BEFORE rating - never as depth added after (the rating is
# concave, so depth-domain addition double-counts; verified 2026-07-10).
# The campus rating is fit at 2580-7305 cfs and under-predicts at low flow
# (gives 2.2 ft where ~4 ft is observed; the constructed channel ponds), so
# an OBSERVED clear-day stage acts as a FLOOR on total stage.
#
# PROVENANCE: yield 2.0 cfs/mi^2 is a provisional SBR baseflow estimate
# (Coweeta-order magnitude) - supersede per basin with SIRENE/CONSEIL obs.

BASEFLOW_YIELD_CFS_PER_SQMI = 2.0   # provisional; supersede with SIRENE obs

QB_CFS = {           # 2.0 cfs/mi^2 x basins.py da_sqmi   [MODELED]
    "CC-UP-503":     10.1,
    "CC-MS-1100":    22.0,
    "CC-TIL-705":    14.1,
    "CC-SPD-1830":   36.6,
    "CC-COX-097":     1.9,
    "CC-LB-171":      3.4,
    "CC-WCU-2260":   45.2,
    "CC-MOUTH-2340": None,   # out of scope
}

STAGE_FLOOR_FT = {   # observed clear-day stage; rating extrapolation floor
    "CC-WCU-2260": 4.0,      # OBSERVED (constructed channel ponds)
}


def baseflow_q(bid):
    return QB_CFS.get(bid) or 0.0


def stage_total_from_q(calib_q_cfs, bid):
    """TOTAL stage (ft above bed): rate (storm Q + baseflow Q), then apply
    the observed low-flow floor. This is what thr_ft compares against."""
    if BASINS[bid]["rating"] == "none":
        return None                      # out of scope (mouth)
    total = depth_from_q((calib_q_cfs or 0.0) + baseflow_q(bid), bid)
    if total is None:
        return None
    return max(total, STAGE_FLOOR_FT.get(bid, 0.0))


def posture_total(calib_q_cfs, bid):
    return posture(stage_total_from_q(calib_q_cfs, bid), bid)


# ----------------------------------------------------------------------------
# 5. FULL CASE  (forecast rain + wetness -> posture, continuous-CN path)
# ----------------------------------------------------------------------------

def assess_wet(bid, qpf_in, wetness, PRF=484.0, dt_hr=0.25):
    """Upgraded per-basin chain: continuous CN + baseflow-inclusive posture.
    Mirrors test_model.run_case per-basin body; that legacy stepped path is
    left untouched for the tabletop harness."""
    import test_model as tm
    b = tm.BASINS[bid]
    from flood_rating import calibrate_peak
    CN = cn_from_wetness(b["CN2"], wetness)
    qp = tm.peak_discharge_cfs(tm.storm_hyetograph(qpf_in, dt_hr=dt_hr),
                               CN, b["DA"], b["Tc"] / 60.0, PRF=PRF, dt_hr=dt_hr)
    cq = calibrate_peak(qp, bid)
    storm = depth_from_q(cq, bid)
    stage = stage_total_from_q(cq, bid)
    return dict(wetness=wetness, CN=CN, qp=qp, calib_q=cq,
                storm_ft=storm, stage_ft=stage, posture=posture(stage, bid))


# ----------------------------------------------------------------------------
# self-test
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 78)
    print("WETNESS ENGINE SELF-TEST")
    print("=" * 78)
    print(f"API_5DAY_EQUIV = {API_5DAY_EQUIV:.4f} (expect ~1.9153)")
    print(f"growing-season API breakpoints: lo={1.4*API_5DAY_EQUIV:.2f}\" "
          f"hi={2.1*API_5DAY_EQUIV:.2f}\"  | dormant: "
          f"lo={0.5*API_5DAY_EQUIV:.2f}\" hi={1.1*API_5DAY_EQUIV:.2f}\"")

    print("\ncontinuous CN (CN2=63):",
          " ".join(f"w={w:.2f}->CN{cn_from_wetness(63, w):5.1f}"
                   for w in (0.0, 0.25, 0.5, 0.75, 1.0)))
    c1, c2, c3 = cn_bounds(63)
    assert abs(cn_from_wetness(63, 0.0) - c1) < 1e-9
    assert abs(cn_from_wetness(63, 0.5) - c2) < 1e-9
    assert abs(cn_from_wetness(63, 1.0) - c3) < 1e-9

    # steady-rain anchoring: 0.28"/day for 30 days == P5 of 1.4" exactly at lo
    api = api_from_daily([0.28] * 30)
    print(f"\nsteady 0.28\"/day: API={api:.2f}\"  w(Jul)="
          f"{wetness_from_api(api, month=7):.3f} (expect ~0.50)")

    # two-week-old wet spell still registers (the old 5-day model forgot it)
    series = [0.0] * 12 + [2.5, 2.0] + [0.0] * 16   # heavy rain 16-17 days ago
    api = api_from_daily(series)
    print(f"wet spell 16d ago: p5=0.00\" (old model: bone dry) | "
          f"API={api:.2f}\" -> w={wetness_from_api(api, month=7):.2f}")

    print("\nbaseflow (zero-storm total stage and posture):")
    for bid in QB_CFS:
        st = stage_total_from_q(0.0, bid)
        p = posture(st, bid)
        print(f"  {bid:14s} Qb={str(QB_CFS[bid]):>5} cfs  "
              f"stage={('%.2f' % st) if st is not None else '  --'} ft  -> {p}")
        if st is not None:
            assert p in ("NORMAL", "N/A"), f"{bid} baseflow alone breaches WATCH!"

    print("\nfull chain, 10-yr storm (4.8\") on WCU campus:")
    for w in (0.0, 0.5, 1.0):
        r = assess_wet("CC-WCU-2260", 4.8, w)
        print(f"  w={w:.1f}: CN={r['CN']:5.1f} calibQ={r['calib_q']:6.0f} "
              f"storm={r['storm_ft']:.2f}ft total={r['stage_ft']:.2f}ft "
              f"-> {r['posture']}")
    print("=" * 78)
    print("PORT NOTE: the JS engine in Cullowhee_Creek_live_status.html mirrors")
    print("this module (cnFromWetness / wetnessFromAPI / soil percentile / d0).")
    print("=" * 78)
