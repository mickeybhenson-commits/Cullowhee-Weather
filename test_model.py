"""
test_model.py  —  Cullowhee Creek flood-warning TEST HARNESS
============================================================
Runs the full rainfall -> runoff -> peak-Q -> stage -> posture chain on
SYNTHETIC design storms and/or HISTORICAL rainfall, with NO sensors required.

Purpose: exercise and validate the logic before any hardware is deployed, and
demonstrate the antecedent-moisture module (SSURGO curve number + 5-day ARC +
daily API accounting) that lets soil type and 7-day rain history sharpen the
runoff conversion from public data alone.

This file is SELF-CONTAINED. It does not import your engine, so it will not
clobber flood_engine.py / flood_network.py / flood_profile.py / orographic.py.
Drop it in the repo root and run:  python test_model.py

Everything in CAPS_DEFAULTS below is a placeholder calibrated from the roster
work in this project — replace with your authoritative values as you confirm
them (Atlas-14 depths, bankfull geometry, CN, HDc application direction).
"""

import math

# ----------------------------------------------------------------------------
# 1. SUB-BASIN TABLE  (from the Cullowhee roster + Tc analysis in this project)
#    Tc_min: representative time of concentration (TR-55/Kirpich, this project)
#    lead:   'limited' (Tc<120, forecast-triggered) or 'adequate' (sensor-triggered)
#    Bankfull geometry from Henson et al. (2014) regional curves / FIS — EDIT.
# ----------------------------------------------------------------------------
BASINS = {
    # id            DA     Tc_min  CN2  lead       bf_w  bf_d  n      slope    thr_type
    "CC-UP-503":   dict(DA=5.35,  Tc=40,  CN2=63, lead="limited", w=30.0, d=1.78, n=0.060, s=0.0888, thr="bankfull"),
    "CC-MS-1100":  dict(DA=11.03, Tc=63,  CN2=63, lead="limited", w=45.7, d=2.32, n=0.045, s=0.0446, thr="bankfull"),
    "CC-TIL-705":  dict(DA=7.05,  Tc=62,  CN2=63, lead="limited", w=37.0, d=2.03, n=0.050, s=0.0547, thr="bankfull"),
    "CC-SPD-1830": dict(DA=18.3,  Tc=62,  CN2=63, lead="limited", w=55.7, d=2.71, n=0.045, s=0.0425, thr="bankfull"),
    "CC-COX-097":  dict(DA=0.97,  Tc=29,  CN2=66, lead="limited", w=14.0, d=1.10, n=0.060, s=0.1000, thr="bankfull"),
    "CC-LB-171":   dict(DA=1.71,  Tc=36,  CN2=65, lead="limited", w=18.0, d=1.30, n=0.055, s=0.0753, thr="bankfull"),
    "CC-WCU-2260": dict(DA=22.6,  Tc=127, CN2=64, lead="adequate",w=40.0, d=12.0, n=0.035, s=0.0050, thr="campus"),
    "CC-MOUTH-2340":dict(DA=23.4, Tc=147, CN2=64, lead="adequate",w=60.0, d=2.90, n=0.045, s=0.0050, thr="bankfull"),
}

# Optional: override CN2 with imagery-derived, field-calibrated curve numbers
# produced by landuse_cn.py (cn_overrides.json). Falls back silently to the
# defaults above if the file isn't present.
try:
    import json as _json, os as _os
    if _os.path.exists("cn_overrides.json"):
        for _bid, _cn in _json.load(open("cn_overrides.json")).items():
            if _bid in BASINS:
                BASINS[_bid]["CN2"] = float(_cn)
except Exception:
    pass

# Campus 7/9/11 ft posture ladder (depth above bed). For bankfull-type reaches,
# WATCH = 0.75*bankfull for lead-limited (the offset from this project), WARNING
# = bankfull, EMERGENCY = bankfull + freeboard placeholder (replace w/ surveyed
# receptor elevation when you have benchmarks).
# Posture thresholds = stage (ft above bed) for WATCH / WARNING / EMERGENCY.
# Campus uses the real 7/9/11 wall ladder. The bankfull reaches are SEEDED from
# flood-frequency stages (WATCH≈2-yr, WARNING≈10-yr, EMERGENCY≈50-yr, at ARC-II);
# lead-limited reaches' WATCH is dropped 0.75x to fire earlier.
# >>> REPLACE each `emergency` with the surveyed lowest-receptor elevation
#     (NAVD88, relative to bed) once you have benchmarks — that's the real number.
THRESHOLDS = {
    "CC-UP-503":    dict(watch=1.60, warning=4.63, emergency=7.37),
    "CC-MS-1100":   dict(watch=1.84, warning=5.31, emergency=8.46),
    "CC-TIL-705":   dict(watch=1.56, warning=4.48, emergency=7.14),
    "CC-SPD-1830":  dict(watch=2.37, warning=6.82, emergency=10.88),
    "CC-COX-097":   dict(watch=0.97, warning=2.54, emergency=3.97),
    "CC-LB-171":    dict(watch=1.19, warning=3.18, emergency=4.90),
    "CC-WCU-2260":  dict(watch=7.0,  warning=9.0,  emergency=11.0),   # real wall ladder
    "CC-MOUTH-2340":dict(watch=5.81, warning=11.93, emergency=19.19),
}

# 24-hour design-storm depths (inches) for the Cullowhee valley. PLACEHOLDERS —
# replace with NOAA Atlas-14 point values for the gauge location, then let
# orographic.py scale them up by elevation per sub-basin.
DESIGN_DEPTH_IN = {"2-yr": 3.2, "10-yr": 4.8, "25-yr": 5.8, "50-yr": 6.6, "100-yr": 7.5}

GROWING_SEASON = True   # True May-Oct (higher ET, drier default); flips ARC thresholds


# ----------------------------------------------------------------------------
# 2. ANTECEDENT MOISTURE  (the module we discussed — public data, no sensors)
# ----------------------------------------------------------------------------
def arc_class(p5_in, growing=GROWING_SEASON):
    """NRCS Antecedent Runoff Condition from 5-day antecedent rainfall (inches)."""
    if growing:
        if p5_in < 1.4:  return 1   # dry
        if p5_in <= 2.1: return 2   # normal
        return 3                    # wet
    else:  # dormant
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
    """Antecedent Precipitation Index: API_t = k*API_{t-1} + P_t.
    A continuous alternative to the 5-day bucket for live/historical accounting.
    k ~ 0.85-0.92 (daily recession). Returns list of API after each day."""
    api, out = api0, []
    for p in daily_rain_in:
        api = k * api + p
        out.append(api)
    return out


# ----------------------------------------------------------------------------
# 3. DESIGN STORM  (SCS Type II 24-hr mass curve, interpolated to sub-hourly)
# ----------------------------------------------------------------------------
_TYPE2 = [0.000,0.011,0.022,0.035,0.048,0.064,0.080,0.098,0.120,0.147,0.181,0.235,
          0.663,0.772,0.820,0.854,0.880,0.902,0.921,0.938,0.953,0.967,0.984,1.000]

def storm_hyetograph(total_in, dt_hr=0.25):
    """Incremental rainfall (in) per dt over a 24-hr Type II storm,
    linearly interpolating the hourly mass curve to dt resolution."""
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
    """SCS-CN direct runoff depth (in). Ia = 0.2 S."""
    S = 1000.0 / CN - 10.0
    if P_in <= 0.2 * S:
        return 0.0
    return (P_in - 0.2 * S) ** 2 / (P_in + 0.8 * S)


# ----------------------------------------------------------------------------
# 5. PEAK DISCHARGE  (unit-hydrograph CONVOLUTION — respects storm time structure)
#    Build a triangular UH (per inch of runoff), apply CN to CUMULATIVE rainfall
#    to get incremental runoff each dt, convolve, take the peak.
#    PRF 484 standard; raise toward ~600 for steep mountain basins.
# ----------------------------------------------------------------------------
def incremental_runoff(hyeto_in, CN):
    cum_p, s = [], 0.0
    for p in hyeto_in:
        s += p; cum_p.append(s)
    cum_q = [runoff_depth_in(P, CN) for P in cum_p]
    return [cum_q[0]] + [cum_q[i] - cum_q[i - 1] for i in range(1, len(cum_q))]

def unit_hydrograph(DA_sqmi, Tc_hr, PRF=484.0, dt_hr=0.25):
    """Triangular UH ordinates (cfs per inch of runoff) at dt spacing."""
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


# 6. STAGE FROM DISCHARGE  (Manning rectangular rating + HDc correction)
#    HDc = 1.05 * DA^-0.14 (Henson-Dietz, Ecoregion 66).
#    NOTE: the *direction* of the HDc correction should match your manuscript;
#    here it scales the Manning conveyance. Verify and flip if needed.
# ----------------------------------------------------------------------------
def hdc(DA_sqmi):
    return 1.05 * DA_sqmi ** (-0.14)

def manning_q(depth_ft, b):
    """Discharge for a rectangular channel of width w at given depth, with HDc."""
    w, n, s = b["w"], b["n"], b["s"]
    A = w * depth_ft
    P = w + 2 * depth_ft
    R = A / P if P > 0 else 0.0
    q = (1.49 / n) * A * R ** (2.0 / 3.0) * s ** 0.5
    return q * hdc(b["DA"])

def stage_from_q(qp_cfs, b, dmax=40.0):
    """Invert the Manning+HDc rating for depth (ft) by bisection."""
    lo, hi = 0.0, dmax
    if manning_q(hi, b) < qp_cfs:
        return hi  # off the top of the rating
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if manning_q(mid, b) < qp_cfs:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ----------------------------------------------------------------------------
# 7. POSTURE  (compare stage to the ladder for this reach)
# ----------------------------------------------------------------------------
def posture(stage_ft, b, basin_id=None):
    t = THRESHOLDS.get(basin_id) if basin_id else None
    if t is None:   # fallback if a basin isn't in THRESHOLDS
        bf = b["d"]
        t = dict(watch=(0.75 * bf if b["lead"] == "limited" else bf),
                 warning=bf, emergency=2.5 * bf)
    if stage_ft >= t["emergency"]: return "EMERGENCY"
    if stage_ft >= t["warning"]:   return "WARNING"
    if stage_ft >= t["watch"]:     return "WATCH"
    return "NORMAL"


# ----------------------------------------------------------------------------
# 8. ONE CASE  (storm depth + antecedent 5-day rain -> posture per basin)
# ----------------------------------------------------------------------------
def run_case(total_depth_in, p5_in, PRF=484.0, dt_hr=0.25):
    arc = arc_class(p5_in)
    hyeto = storm_hyetograph(total_depth_in, dt_hr=dt_hr)
    out = {}
    for bid, b in BASINS.items():
        CN = cn_adjust(b["CN2"], arc)
        Q = runoff_depth_in(total_depth_in, CN)
        qp = peak_discharge_cfs(hyeto, CN, b["DA"], b["Tc"] / 60.0, PRF=PRF, dt_hr=dt_hr)
        stage = stage_from_q(qp, b)
        out[bid] = dict(CN=CN, Q=Q, qp=qp, stage=stage, posture=posture(stage, b, bid))
    return arc, out


# ----------------------------------------------------------------------------
# 9. TEST MATRIX  (design storms x antecedent conditions)
# ----------------------------------------------------------------------------
def run_matrix():
    antecedents = [("DRY  (P5=0.2\")", 0.2), ("NORMAL (P5=1.7\")", 1.7), ("WET  (P5=3.0\")", 3.0)]
    print("=" * 78)
    print("CULLOWHEE FLOOD ENGINE — TABLETOP TEST  (no sensors; synthetic storms)")
    print("=" * 78)
    for sname, depth in DESIGN_DEPTH_IN.items():
        print(f"\n### {sname} design storm — {depth}\" / 24 hr (SCS Type II) ###")
        for aname, p5 in antecedents:
            arc, res = run_case(depth, p5)
            tag = {1: "ARC-I dry", 2: "ARC-II", 3: "ARC-III wet"}[arc]
            print(f"\n  antecedent {aname}  -> {tag}")
            print(f"    {'basin':14s} {'CN':>5} {'runoff_in':>9} {'peak_cfs':>9} {'stage_ft':>8}  posture")
            for bid, r in res.items():
                print(f"    {bid:14s} {r['CN']:5.0f} {r['Q']:9.2f} {r['qp']:9.0f} {r['stage']:8.2f}  {r['posture']}")
    print("\n" + "=" * 78)
    print("Antecedent effect demo: same storm, DRY vs WET soils -> different posture.")
    print("Drives the point that soil type + 5-day rain (public data) reshape runoff")
    print("with zero sensors. Replace CAPS placeholders with confirmed values.")
    print("=" * 78)


# ----------------------------------------------------------------------------
# 10. HISTORICAL REPLAY  (feed a real daily-rain series; e.g. a past event)
#     Supply [(date, daily_in), ...]; uses API accounting for antecedent state
#     and treats the peak-rain day as the event. Hook your archived QPF/obs here.
# ----------------------------------------------------------------------------
def replay(daily_rain, k=0.90):
    """daily_rain: list of (label, inches). Returns posture on the peak day,
    using prior 5 days as antecedent."""
    rains = [r for _, r in daily_rain]
    api = api_series(rains, k=k)
    peak_i = max(range(len(rains)), key=lambda i: rains[i])
    p5 = sum(rains[max(0, peak_i - 5):peak_i])     # 5 days before the peak
    arc, res = run_case(rains[peak_i], p5)
    print(f"\nHISTORICAL REPLAY — peak day '{daily_rain[peak_i][0]}' = {rains[peak_i]}\"")
    print(f"  5-day antecedent before peak = {p5:.2f}\"  (API={api[peak_i]:.2f})  -> ARC-{arc}")
    print(f"  {'basin':14s} {'stage_ft':>8}  posture")
    for bid, r in res.items():
        print(f"  {bid:14s} {r['stage']:8.2f}  {r['posture']}")
    return res


if __name__ == "__main__":
    run_matrix()

    # Example historical replay — replace with a real event's daily rainfall.
    demo_event = [("d-6", 0.3), ("d-5", 0.8), ("d-4", 1.2), ("d-3", 2.1),
                  ("d-2", 1.6), ("d-1", 3.4), ("PEAK", 9.8), ("d+1", 2.2)]
    replay(demo_event)
