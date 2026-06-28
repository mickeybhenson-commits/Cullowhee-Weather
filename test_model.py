"""
test_model.py  —  Cullowhee Creek flood-warning TEST HARNESS  (engine-integrated)
=================================================================================
Runs the full rainfall -> runoff -> peak-Q -> stage -> posture chain on
SYNTHETIC design storms and/or HISTORICAL rainfall, with NO sensors required.

WHAT CHANGED (integration):
  The rainfall -> runoff -> PEAK DISCHARGE half is unchanged - that is the
  forecast-driven, lead-time engine. The DISCHARGE -> STAGE -> POSTURE tail now
  calls the shared engine instead of a local rating:

      qp    = peak_discharge_cfs(...)        # raw TR-55/UH peak (unchanged)
      cq    = calibrate_peak(qp, bid)        # bias-correct onto regression  (basins.py)
      stage = depth_from_q(cq, bid)          # TVA rating (campus) / rectangle (others)
      post  = posture(stage, bid)            # vs basins.py thresholds

  The old manning_q / hdc / stage_from_q / THRESHOLDS / EFFECTIVE_THRESHOLDS /
  BASEFLOW_DEPTH are GONE - one rating now, in flood_rating.py, fed by basins.py.

REQUIRES basins.py and flood_rating.py in the same directory.

COUPLING NOTES (read before changing numbers):
  - The per-basin calibration in basins.py was fit to the peaks THIS file
    produces from the DA/CN/Tc below. If you change a basin's DA, CN, or Tc,
    refit that basin's `calib` (see basins.py calib_anchors).
  - DA here still carries the older roster values (e.g. UP-503 = 5.35); the
    StreamStats/buildsheet authoritative DA is in basins.py (5.03). They are
    reconciled only through the calibration. To unify, set these DA to the
    basins.py values and refit calib.
  - Thresholds for the 7 non-campus reaches are PLACEHOLDERS in basins.py
    (bankfull-referenced); their posture is provisional until surveyed receptors.
"""

import math
from flood_rating import calibrate_peak, depth_from_q, posture

# ----------------------------------------------------------------------------
# 1. SUB-BASIN DISCHARGE INPUTS  (rainfall -> peak Q only)
#    Bankfull geometry, ratings, thresholds, and per-basin calibration now live
#    in basins.py. This table holds ONLY what the peak-discharge chain needs:
#    DA (mi^2), Tc (min), CN(II), and the lead class.
# ----------------------------------------------------------------------------
BASINS = {
    # id             DA      Tc    CN2  lead
    "CC-UP-503":    dict(DA=5.35,  Tc=40,  CN2=63, lead="limited"),
    "CC-MS-1100":   dict(DA=11.03, Tc=63,  CN2=63, lead="limited"),
    "CC-TIL-705":   dict(DA=7.05,  Tc=62,  CN2=63, lead="limited"),
    "CC-SPD-1830":  dict(DA=18.3,  Tc=62,  CN2=63, lead="limited"),
    "CC-COX-097":   dict(DA=0.97,  Tc=29,  CN2=66, lead="limited"),
    "CC-LB-171":    dict(DA=1.71,  Tc=36,  CN2=65, lead="limited"),
    "CC-WCU-2260":  dict(DA=22.6,  Tc=127, CN2=64, lead="adequate"),
    "CC-MOUTH-2340":dict(DA=23.4,  Tc=147, CN2=64, lead="adequate"),
}

# Optional: override CN2 with imagery-derived curve numbers (landuse_cn.py).
try:
    import json as _json, os as _os
    if _os.path.exists("cn_overrides.json"):
        for _bid, _cn in _json.load(open("cn_overrides.json")).items():
            if _bid in BASINS:
                BASINS[_bid]["CN2"] = float(_cn)
except Exception:
    pass

# 24-hour design-storm depths (inches). PLACEHOLDERS - replace with NOAA Atlas-14
# point values, then let orographic.py scale by elevation per sub-basin.
# (StreamStats I24H50Y for the campus came back 8.53 in; the 50-yr here is low.)
DESIGN_DEPTH_IN = {"2-yr": 3.2, "10-yr": 4.8, "25-yr": 5.8, "50-yr": 6.6, "100-yr": 7.5}

GROWING_SEASON = True   # True May-Oct (higher ET, drier default); flips ARC thresholds


# ----------------------------------------------------------------------------
# 2. ANTECEDENT MOISTURE  (public data, no sensors)
# ----------------------------------------------------------------------------
def arc_class(p5_in, growing=GROWING_SEASON):
    """NRCS Antecedent Runoff Condition from 5-day antecedent rainfall (inches)."""
    if growing:
        if p5_in < 1.4:  return 1
        if p5_in <= 2.1: return 2
        return 3
    else:
        if p5_in < 0.5:  return 1
        if p5_in <= 1.1: return 2
        return 3


def cn_adjust(cn2, arc):
    """Convert CN(II) to CN(I) dry or CN(III) wet (NRCS standard relations)."""
    if arc == 1:
        return cn2 / (2.281 - 0.01281 * cn2)
    if arc == 3:
        return cn2 / (0.427 + 0.00573 * cn2)
    return cn2


def api_series(daily_rain_in, k=0.90, api0=0.0):
    """Antecedent Precipitation Index: API_t = k*API_{t-1} + P_t."""
    api, out = api0, []
    for p in daily_rain_in:
        api = k * api + p
        out.append(api)
    return out


# ----------------------------------------------------------------------------
# 3. DESIGN STORM  (SCS Type II 24-hr mass curve)
# ----------------------------------------------------------------------------
_TYPE2 = [0.000,0.011,0.022,0.035,0.048,0.064,0.080,0.098,0.120,0.147,0.181,0.235,
          0.663,0.772,0.820,0.854,0.880,0.902,0.921,0.938,0.953,0.967,0.984,1.000]

def storm_hyetograph(total_in, dt_hr=0.25):
    steps = int(round(24.0 / dt_hr))
    cum = []
    for k in range(steps + 1):
        h = k * dt_hr
        i = min(int(h), 23)
        frac = h - i
        c = _TYPE2[i] + frac * (_TYPE2[min(i + 1, 23)] - _TYPE2[i])
        cum.append(c * total_in)
    return [cum[i + 1] - cum[i] for i in range(len(cum) - 1)]


# 4. RUNOFF  (SCS Curve Number)
# ----------------------------------------------------------------------------
def runoff_depth_in(P_in, CN):
    S = 1000.0 / CN - 10.0
    if P_in <= 0.2 * S:
        return 0.0
    return (P_in - 0.2 * S) ** 2 / (P_in + 0.8 * S)


# 5. PEAK DISCHARGE  (triangular-UH convolution)
# ----------------------------------------------------------------------------
def incremental_runoff(hyeto_in, CN):
    cum_p, s = [], 0.0
    for p in hyeto_in:
        s += p; cum_p.append(s)
    cum_q = [runoff_depth_in(P, CN) for P in cum_p]
    return [cum_q[0]] + [cum_q[i] - cum_q[i - 1] for i in range(1, len(cum_q))]

def unit_hydrograph(DA_sqmi, Tc_hr, PRF=484.0, dt_hr=0.25):
    Tp = 0.6 * Tc_hr + dt_hr / 2.0
    Tb = 2.67 * Tp
    qp = PRF * DA_sqmi / Tp
    ords, t = [], 0.0
    while t <= Tb:
        q = qp * t / Tp if t <= Tp else qp * (Tb - t) / (Tb - Tp)
        ords.append(max(q, 0.0)); t += dt_hr
    return ords

def peak_discharge_cfs(hyeto_in, CN, DA_sqmi, Tc_hr, PRF=484.0, dt_hr=0.25):
    incr = incremental_runoff(hyeto_in, CN)
    uh = unit_hydrograph(DA_sqmi, Tc_hr, PRF=PRF, dt_hr=dt_hr)
    h = [0.0] * (len(incr) + len(uh))
    for i, r in enumerate(incr):
        if r <= 0:
            continue
        for j, u in enumerate(uh):
            h[i + j] += r * u
    return max(h)


# ----------------------------------------------------------------------------
# 6. STAGE + POSTURE  -> now the shared engine (flood_rating.py / basins.py).
#    Discharge is calibrated onto regression per basin, then rated (TVA for the
#    campus, in-bank rectangle for the tributaries) and classified.
#    No local Manning/HDc/threshold code remains here.
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
# 7. ONE CASE  (storm depth + antecedent 5-day rain -> posture per basin)
# ----------------------------------------------------------------------------
def run_case(total_depth_in, p5_in, PRF=484.0, dt_hr=0.25):
    arc = arc_class(p5_in)
    hyeto = storm_hyetograph(total_depth_in, dt_hr=dt_hr)
    out = {}
    for bid, b in BASINS.items():
        CN = cn_adjust(b["CN2"], arc)
        Q = runoff_depth_in(total_depth_in, CN)
        qp = peak_discharge_cfs(hyeto, CN, b["DA"], b["Tc"] / 60.0, PRF=PRF, dt_hr=dt_hr)
        cq = calibrate_peak(qp, bid)              # raw model peak -> regression scale
        stage = depth_from_q(cq, bid)             # engine rating
        out[bid] = dict(CN=CN, Q=Q, qp=qp, calib_q=cq,
                        stage=stage, posture=posture(stage, bid))
    return arc, out


# ----------------------------------------------------------------------------
# 8. TEST MATRIX  (design storms x antecedent conditions)
# ----------------------------------------------------------------------------
def _fmt(stage):
    return f"{stage:8.2f}" if stage is not None else f"{'--':>8}"

def run_matrix():
    antecedents = [("DRY  (P5=0.2\")", 0.2), ("NORMAL (P5=1.7\")", 1.7), ("WET  (P5=3.0\")", 3.0)]
    print("=" * 92)
    print("CULLOWHEE FLOOD ENGINE - TABLETOP TEST  (no sensors; synthetic storms; engine-rated)")
    print("=" * 92)
    for sname, depth in DESIGN_DEPTH_IN.items():
        print(f"\n### {sname} design storm - {depth}\" / 24 hr (SCS Type II) ###")
        for aname, p5 in antecedents:
            arc, res = run_case(depth, p5)
            tag = {1: "ARC-I dry", 2: "ARC-II", 3: "ARC-III wet"}[arc]
            print(f"\n  antecedent {aname}  -> {tag}")
            print(f"    {'basin':14s} {'CN':>5} {'runoff_in':>9} {'model_Q':>8} {'calib_Q':>8} {'stage_ft':>8}  posture")
            for bid, r in res.items():
                print(f"    {bid:14s} {r['CN']:5.0f} {r['Q']:9.2f} {r['qp']:8.0f} {r['calib_q']:8.0f} "
                      f"{_fmt(r['stage'])}  {r['posture']}")
    print("\n" + "=" * 92)
    print("model_Q -> calib_Q is the per-basin regression bias correction (~1.9-2.8x).")
    print("Campus rates on TVA; the 6 tributaries rate in-bank on the rectangle; mouth out of scope.")
    print("Non-campus thresholds are placeholders (basins.py) - those postures are provisional.")
    print("=" * 92)


# ----------------------------------------------------------------------------
# 9. HISTORICAL REPLAY  (feed a real daily-rain series)
# ----------------------------------------------------------------------------
def replay(daily_rain, k=0.90):
    rains = [r for _, r in daily_rain]
    api = api_series(rains, k=k)
    peak_i = max(range(len(rains)), key=lambda i: rains[i])
    p5 = sum(rains[max(0, peak_i - 5):peak_i])
    arc, res = run_case(rains[peak_i], p5)
    print(f"\nHISTORICAL REPLAY - peak day '{daily_rain[peak_i][0]}' = {rains[peak_i]}\"")
    print(f"  5-day antecedent before peak = {p5:.2f}\"  (API={api[peak_i]:.2f})  -> ARC-{arc}")
    print(f"  {'basin':14s} {'calib_Q':>8} {'stage_ft':>8}  posture")
    for bid, r in res.items():
        print(f"  {bid:14s} {r['calib_q']:8.0f} {_fmt(r['stage'])}  {r['posture']}")
    return res


if __name__ == "__main__":
    run_matrix()

    demo_event = [("d-6", 0.3), ("d-5", 0.8), ("d-4", 1.2), ("d-3", 2.1),
                  ("d-2", 1.6), ("d-1", 3.4), ("PEAK", 9.8), ("d+1", 2.2)]
    replay(demo_event)
