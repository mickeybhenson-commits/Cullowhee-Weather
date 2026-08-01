#!/usr/bin/env python3
"""
test_model.py - COMPATIBILITY SHIM over the authoritative engine.

WHY THIS FILE EXISTS
  Three modules still import `test_model`:
      live_rainfall.py       (live per-basin forcing -> posture)
      outlook_engine.py      (the calibrated Outlook hook)
      pages/1_Test_Model.py  (the Streamlit engine view)
  but test_model.py was no longer in the repo, so all three died on import.
  In streamlit_app.py the failure was swallowed by a bare `except Exception:
  pass`, so the app kept running with its live forcing hook silently dead.

  This shim restores that API on top of the modules that DID survive, so there
  is still exactly one implementation of the physics:

      cwm_model.py    rainfall -> SCS Type II hyetograph -> NRCS-CN runoff
                      -> unit hydrograph -> raw peak Q   (the faithful port)
      wetness.py      continuous curve number, 30-day API, baseflow, total stage
      flood_rating.py per-basin calibration -> return period -> POSTURE

  NOTE ON BEHAVIOR: the retired module used the legacy 3-step ARC staircase and
  a stage-vs-threshold posture. This shim runs the CURRENT engine instead -
  continuous CN (wetness.cn_from_wetness) and the 2026-07 §2 frequency
  classification. That is deliberate: those two changes are the corrections that
  fixed a demonstrated Helene under-warning, and callers should not be able to
  reach a superseded posture through a back door. `arc` is still reported, as a
  descriptive label only.

  New code should call forecast.py (live) or flood_rating.assess() (engine)
  directly. This module is here to keep existing callers working, not to grow.
"""

from __future__ import annotations

import cwm_model as cwm
import wetness as wet
from basins import routed_order
from flood_rating import assess, posture_stage

# Re-export the engine's runnable per-basin parameters (DA, Tc, CN2, calib,
# rating, section, thresholds). Single source of truth: cwm_model.
BASINS = cwm.BASINS
ORDER = cwm.ORDER

# Legacy helper names the old module exposed.
storm_hyetograph = cwm.storm_hyetograph


def peak_discharge_cfs(hyeto, CN, DA, TcHr, PRF=484.0, dt_hr=0.25):
    """Legacy signature for cwm_model.peak_discharge."""
    return cwm.peak_discharge(hyeto, CN, DA, TcHr, dt=dt_hr, prf=PRF)


def posture(stage_ft, basin_rec=None, bid=None):
    """Legacy stage-vs-threshold posture. `basin_rec` is accepted and ignored:
    the thresholds come from the registry via `bid`."""
    return posture_stage(stage_ft, bid)


def arc_class(w):
    """Antecedent runoff condition as the legacy INTEGER class 1/2/3.

    Callers depend on the integer form (pages/1_Test_Model.py indexes a
    {1:..,2:..,3:..} map with it), so run_case returns this, not a label. The
    engine itself no longer classifies by ARC at all - the curve number comes
    from the continuous wetness index - so this is descriptive only.
    """
    if w < 1.0 / 3.0:
        return 1
    if w < 2.0 / 3.0:
        return 2
    return 3


ARC_LABEL = {1: "ARC-I (dry)", 2: "ARC-II (normal)", 3: "ARC-III (wet)"}


def arc_label(w):
    """Descriptive antecedent-runoff-condition label for a wetness index."""
    return ARC_LABEL[arc_class(w)]


def run_case(storm_in, antecedent_in, PRF=484.0, dt_hr=0.25, month=None):
    """Sweep every basin for one (storm depth, antecedent) pair.

    `antecedent_in` is the legacy 5-day rainfall total; it is converted onto the
    30-day API scale by wetness.resolve_wetness (the `p5_legacy` rung) so steady
    rain reproduces the classic NRCS behavior.

    Returns (arc_class, {bid: {...}}) - the legacy 2-tuple, with the ARC as the
    integer 1/2/3 the original module emitted. Each record carries
    CN, Q (runoff inches), qp (raw model peak), calib_q, stage (TOTAL stage,
    baseflow-inclusive) and posture (the authoritative engine call).
    """
    w, _src = wet.resolve_wetness(p5_in=antecedent_in, month=month)
    res = {}
    for bid in routed_order():
        b = BASINS[bid]
        cn = wet.cn_from_wetness(b["CN2"], w)
        hyeto = cwm.storm_hyetograph(storm_in, dt=dt_hr)
        _, runoff_in, _ = cwm.incremental_runoff(hyeto, cn)
        qp = cwm.peak_discharge(hyeto, cn, b["DA"], b["Tc"] / 60.0,
                                dt=dt_hr, prf=PRF)
        a = assess(qp, bid)
        res[bid] = {
            "CN": cn,
            "Q": runoff_in,
            "qp": qp,
            "calib_q": a["calib_q"],
            "stage": wet.stage_total_from_q(a["calib_q"], bid),
            "posture": a["posture"],
            "rp_best": a.get("rp_best"),
            "confidence": a.get("confidence"),
            "wetness": w,
        }
    return arc_class(w), res


# ---------------------------------------------------------------------------
# DESIGN STORMS
# ---------------------------------------------------------------------------
# DERIVED, not quoted. Each depth is the 24-h SCS Type II total that drives the
# engine to the USGS regression flow of that return period at the campus
# (CC-WCU-2260) under median wetness (w = 0.5 = ARC II). Solving for the depth
# rather than pasting a NOAA Atlas 14 table keeps every number traceable to
# basins.py, and makes the design-storm view consistent with the frequency
# classification the engine actually posts on.
_DESIGN_RP = [(2, 0.50), (5, 0.20), (10, 0.10), (25, 0.04),
              (50, 0.02), (100, 0.01), (500, 0.002)]


def design_depth_for(rp_aep, bid="CC-WCU-2260", w=0.5, lo=0.1, hi=30.0):
    """24-h depth (in) whose calibrated peak matches this basin's regression
    flow at the given AEP, at wetness w. Bisection on a monotone chain."""
    target = BASINS[bid]["reg_q"][rp_aep]
    b = BASINS[bid]
    cn = wet.cn_from_wetness(b["CN2"], w)

    def q_of(depth):
        qp = cwm.peak_discharge(cwm.storm_hyetograph(depth), cn,
                                b["DA"], b["Tc"] / 60.0)
        return cwm.calibrate_peak(qp, bid)

    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if q_of(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


DESIGN_DEPTH_IN = {f"{rp}-yr": round(design_depth_for(aep), 2)
                   for rp, aep in _DESIGN_RP}


if __name__ == "__main__":
    print("=" * 78)
    print("test_model.py - compatibility shim over cwm_model + wetness + flood_rating")
    print("=" * 78)
    print("DERIVED design storms (24-h depth reproducing the campus regression flow")
    print("at median wetness):")
    for k, v in DESIGN_DEPTH_IN.items():
        print(f"   {k:>7}  {v:5.2f} in")
    print("\nrun_case(10-yr depth, p5=1.7 in):")
    depth = DESIGN_DEPTH_IN["10-yr"]
    arc, res = run_case(depth, 1.7)
    print(f"  antecedent -> ARC class {arc} = {ARC_LABEL[arc]}")
    for bid in routed_order():
        r = res[bid]
        st = f"{r['stage']:.2f} ft" if r["stage"] is not None else "     --"
        print(f"  {bid:15s} CN={r['CN']:5.1f} calibQ={r['calib_q']:6d} "
              f"stage={st:>8}  {r['posture']}")
