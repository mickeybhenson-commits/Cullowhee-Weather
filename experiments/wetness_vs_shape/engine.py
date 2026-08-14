"""
engine.py - faithful port of the NOAH deployed chain (live.html JS / cwm_model),
with one addition: within-event wetness accounting as a continuous knob.

Chain (per noah_per_basin_model_explained.md):
  1 rain -> 2 wetness w -> 3 cnFromWetness(CN2,w) -> 4 NRCS runoff
  -> 5 NRCS dimensionless UH (PRF 484, Tp = 0.6Tc + dt/2) -> 6 calib a*Q^b
  -> 7 classification (campus = TVA stage vs 7/9/11; others = RP on reg_q)

ENGINE params (DA, Tc, CN2) come from test_model.BASINS -- these are what
`calib` was fitted against (noah_model_repairs_2026-08-03.md). basins.py
supplies calib / reg_q / tva_wse / bed_ft / thr_ft / section.

BETA is the only new parameter:
  beta = 0.0 -> storage S is frozen for the storm == the deployed engine, exactly
  beta = 1.0 -> every inch that infiltrates consumes an inch of retention storage
"""
import math

# ---- engine basin params (test_model.py BASINS -- the calibration's own view) --
ENGINE = {
    "CC-UP-503":     dict(DA=5.35,  Tc=40,  CN2=63),
    "CC-MS-1100":    dict(DA=11.03, Tc=63,  CN2=63),
    "CC-TIL-705":    dict(DA=7.05,  Tc=62,  CN2=63),
    "CC-SPD-1830":   dict(DA=18.3,  Tc=62,  CN2=63),
    "CC-COX-097":    dict(DA=0.97,  Tc=29,  CN2=66),
    "CC-LB-171":     dict(DA=1.71,  Tc=36,  CN2=65),
    "CC-WCU-2260":   dict(DA=22.6,  Tc=127, CN2=64),
    "CC-MOUTH-2340": dict(DA=23.4,  Tc=147, CN2=64),
}

# ---- registry values needed downstream (basins.py) ---------------------------
CALIB = {
    "CC-UP-503":     (1.449, 0.815), "CC-MS-1100":    (2.777, 0.760),
    "CC-TIL-705":    (2.241, 0.784), "CC-SPD-1830":   (3.404, 0.739),
    "CC-COX-097":    (0.600, 0.940), "CC-LB-171":     (0.677, 0.921),
    "CC-WCU-2260":   (4.222, 0.744), "CC-MOUTH-2340": (4.610, 0.742),
}
REG_Q = {
    "CC-UP-503":   {0.50:269, 0.20:504, 0.10:705, 0.04:987, 0.02:1250, 0.01:1500, 0.005:1780, 0.002:2160},
    "CC-MS-1100":  {0.50:532, 0.20:965, 0.10:1330, 0.04:1830, 0.02:2290, 0.01:2740, 0.005:3220, 0.002:3870},
    "CC-TIL-705":  {0.50:361, 0.20:667, 0.10:927, 0.04:1290, 0.02:1620, 0.01:1950, 0.005:2300, 0.002:2780},
    "CC-SPD-1830": {0.50:829, 0.20:1470, 0.10:2010, 0.04:2740, 0.02:3410, 0.01:4050, 0.005:4740, 0.002:5660},
    "CC-COX-097":  {0.50:64.3, 0.20:129, 0.10:186, 0.04:269, 0.02:347, 0.01:426, 0.005:513, 0.002:631},
    "CC-LB-171":   {0.50:105, 0.20:206, 0.10:294, 0.04:421, 0.02:539, 0.01:658, 0.005:788, 0.002:964},
    "CC-WCU-2260": {0.50:996, 0.20:1750, 0.10:2380, 0.04:3230, 0.02:4010, 0.01:4760, 0.005:5560, 0.002:6630},
    "CC-MOUTH-2340":{0.50:1030, 0.20:1800, 0.10:2450, 0.04:3320, 0.02:4120, 0.01:4880, 0.005:5710, 0.002:6800},
}
AEP_RP = [(0.50,2),(0.20,5),(0.10,10),(0.04,25),(0.02,50),(0.01,100),(0.005,200),(0.002,500)]
SECTION = {
    "CC-UP-503":   dict(w=29.7, n=0.045, s=0.0888), "CC-MS-1100":  dict(w=45.7, n=0.045, s=0.0446),
    "CC-TIL-705":  dict(w=38.4, n=0.050, s=0.0547), "CC-SPD-1830": dict(w=55.7, n=0.045, s=0.0425),
    "CC-COX-097":  dict(w=15.0, n=0.045, s=0.1000), "CC-LB-171":   dict(w=19.0, n=0.045, s=0.0753),
}
# campus TVA rating
TVA_WSE_CAMPUS = {10:(2580, 2079.2), 100:(5155, 2081.5), 500:(7305, 2082.9)}
BED_CAMPUS = 2070.5
CAMPUS_THR = (7.0, 9.0, 11.0)          # VALIDATED: 11 ft = water in road
WATCH_1_5YR = {"CC-COX-097", "CC-LB-171"}

# SCS Type II 24-h cumulative, hourly (test_model._TYPE2)
_TYPE2 = [0.000,0.011,0.022,0.035,0.048,0.064,0.080,0.098,0.120,0.147,0.181,0.235,
          0.663,0.772,0.820,0.854,0.880,0.902,0.921,0.938,0.953,0.967,0.984,1.000]


# ---- 3. curve number ---------------------------------------------------------
def cn_arc1(cn2):  return cn2 / (2.281 - 0.01281 * cn2)
def cn_arc3(cn2):  return cn2 / (0.427 + 0.00573 * cn2)

def cn_from_wetness(cn2, w):
    """Continuous slide between ARC-I and ARC-III bounds (live.html cnFromWetness).
    w=1 must equal ARC-III exactly -- verified against the published worked example."""
    lo, hi = cn_arc1(cn2), cn_arc3(cn2)
    return lo + max(0.0, min(1.0, w)) * (hi - lo)

def s_from_cn(cn):  return 1000.0 / cn - 10.0


# ---- 1. hyetographs ----------------------------------------------------------
def type2_hyetograph(total_in, dt_hr=0.25):
    steps = int(round(24.0 / dt_hr))
    cum = []
    for k in range(steps + 1):
        h = k * dt_hr
        i = min(int(h), 23)
        frac = h - i
        cum.append((_TYPE2[i] + frac * (_TYPE2[min(i+1,23)] - _TYPE2[i])) * total_in)
    return [cum[i+1] - cum[i] for i in range(len(cum)-1)]

def real_hyetograph(hourly_in, dt_hr=0.25):
    per = max(1, int(round(1.0/dt_hr)))
    out = []
    for v in hourly_in:
        out += [float(v)/per] * per
    return out

def uniform_hyetograph(total_in, hours, dt_hr=0.25):
    steps = int(round(hours/dt_hr))
    return [total_in/steps] * steps


# ---- 4. runoff, static and dynamic-storage forms ------------------------------
def incremental_runoff_static(hyeto, cn):
    """Deployed behaviour: S frozen at the storm's initial wetness."""
    S = s_from_cn(cn)
    Ia = 0.2 * S
    P = 0.0; qprev = 0.0; out = []
    for p in hyeto:
        P += p
        Pe = max(0.0, P - Ia)
        Q = Pe*Pe/(Pe + S) if Pe > 0 else 0.0     # identical to (P-0.2S)^2/(P+0.8S)
        Q = max(Q, qprev)
        out.append(Q - qprev); qprev = Q
    return out

def incremental_runoff_dynamic(hyeto, cn0, cn_sat, beta=1.0, drain_in_hr=0.0,
                               dt_hr=0.25, sub=20):
    """Within-event wetness accounting, mass-consistent.

    State is S, the potential maximum retention (inches). Every inch that
    infiltrates consumes beta inches of remaining storage; storage recovers at
    drain_in_hr between bursts, and is floored at the basin's ARC-III storage --
    the soil cannot get wetter than saturated.

    2026-08-13: replaced with the form derived in runoff_dynamic_fixed.py on
    2026-08-12. The previous version re-evaluated the CUMULATIVE SCS closed form
    F = S*Pe/(Pe+S) using the CURRENT S at every step. F increases with S, so
    shrinking S retroactively restated infiltration that had already happened at
    a drier soil state -- and with it, runoff that had already been routed through
    the unit hydrograph. Three symptoms, all reproduced before this change:

      * dF went negative and was silenced by a max(0, .) clamp
      * cumulative runoff jumped when S dropped, injecting a spurious increment
      * THE PEAK WAS NON-MONOTONE IN BETA. On the Helene case at w0 = 0.53 it rose
        to a false maximum of 2,261 cfs at beta = 2.00, then fell to 2,174 and
        plateaued. Bisecting on beta against that response returns values that do
        not reproduce their target -- which happened, and the (w0, beta) "roots"
        it produced were published before being caught.

    THE FIX: integrate the SCS derivative instead of re-evaluating its integral.

        dQ/dPe = 1 - (S/(Pe+S))^2       dF = dPe - dQ       S <- S - beta*dF

    Runoff already banked is never revised, dF cannot be negative so no clamp is
    needed, and mass is conserved by construction: dQ + dF == dPe exactly.

    beta = 0 reproduces incremental_runoff_static to discretisation error only:
    on the Helene case, 0.17% at the default sub=20, 0.03% at sub=100, 0.002% at
    sub=2000. runoff_dynamic_fixed.py quotes 0.04% for this check, which is the
    sub=100 figure rather than the default -- measured and corrected 2026-08-13.
    Ia is fixed at 0.2*S0 -- initial abstraction is a storm-onset quantity, so a
    storm starting on drier soil forfeits that depth permanently. That is correct
    SCS behaviour and is why within-event wetting cannot beat the static saturated
    bound; see noah_helene_ridge_is_frozen_wetness_2026-08-12.md.

    sub controls discretisation only. Returns (incremental_runoff, S_trace).
    """
    S0 = s_from_cn(cn0)
    S_min = s_from_cn(cn_sat)
    S = S0
    Ia = 0.2 * S0
    P = 0.0
    Pe = 0.0
    Q = 0.0
    out, strace = [], []
    for p in hyeto:
        q0 = Q
        for _ in range(sub):
            dp = p / sub
            P += dp
            newPe = max(0.0, P - Ia)
            dPe = newPe - Pe
            Pe = newPe
            if dPe <= 0.0:
                continue
            frac = 1.0 - (S / (Pe + S)) ** 2 if (Pe + S) > 0 else 1.0
            dQ = dPe * frac
            dF = dPe - dQ
            Q += dQ
            S = min(S0, max(S_min, S - beta * dF + drain_in_hr * dt_hr / sub))
        out.append(Q - q0)
        strace.append(S)
    return out, strace


def unit_hydrograph(DA, Tc_hr, PRF=484.0, dt_hr=0.25):
    Tp = 0.6*Tc_hr + dt_hr/2.0
    Tb = 2.67*Tp
    qp = PRF*DA/Tp
    ords, t = [], 0.0
    while t <= Tb:
        ords.append(max(qp*t/Tp if t <= Tp else qp*(Tb-t)/(Tb-Tp), 0.0))
        t += dt_hr
    return ords

def peak_from_incr(incr, DA, Tc_hr, PRF=484.0, dt_hr=0.25):
    uh = unit_hydrograph(DA, Tc_hr, PRF, dt_hr)
    h = [0.0]*(len(incr)+len(uh))
    for i, r in enumerate(incr):
        if r <= 0: continue
        for j, u in enumerate(uh):
            h[i+j] += r*u
    return max(h)


# ---- 6/7. calibration + classification ---------------------------------------
def calibrate_peak(q, bid):
    a, b = CALIB[bid]
    return a * q**b if q > 0 else 0.0

def rp_from_q(q, bid):
    reg = REG_Q[bid]
    pts = sorted(((reg[a], rp) for a, rp in AEP_RP), key=lambda t: t[0])
    if q <= pts[0][0]:  return pts[0][1]*q/pts[0][0]
    if q >= pts[-1][0]: return pts[-1][1]
    for i in range(len(pts)-1):
        (q0,r0),(q1,r1) = pts[i], pts[i+1]
        if q0 <= q <= q1:
            f = (math.log(q)-math.log(q0))/(math.log(q1)-math.log(q0))
            return r0 + f*(r1-r0)
    return None

def campus_stage_ft(q):
    """TVA log-log rating fit, ft above bed (flood_rating._tva_rating)."""
    pts = [(qq, wse - BED_CAMPUS) for qq, wse in TVA_WSE_CAMPUS.values()]
    lx = [math.log(d) for _, d in pts]; ly = [math.log(qq) for qq, _ in pts]
    n = len(lx); mx = sum(lx)/n; my = sum(ly)/n
    B = sum((x-mx)*(y-my) for x, y in zip(lx, ly)) / sum((x-mx)**2 for x in lx)
    C = math.exp(my - B*mx)
    return (q/C)**(1.0/B) if q > 0 else 0.0

def campus_posture(depth):
    w_, wa, em = CAMPUS_THR
    return "EMERGENCY" if depth >= em else "WARNING" if depth >= wa else "WATCH" if depth >= w_ else "NORMAL"

def posture_from_rp(T, bid):
    if T is None: return "N/A"
    watch = 1.5 if bid in WATCH_1_5YR else 2
    return "EMERGENCY" if T >= 100 else "WARNING" if T >= 10 else "WATCH" if T >= watch else "NORMAL"


# ---- top-level ---------------------------------------------------------------
def run(bid, hyeto, w, beta=0.0, drain_in_hr=0.0, dt_hr=0.25):
    """One basin, one hyetograph, one initial wetness. beta=0 == deployed engine."""
    e = ENGINE[bid]
    cn0 = cn_from_wetness(e["CN2"], w)
    cn_sat = cn_arc3(e["CN2"])
    if beta == 0.0 and drain_in_hr == 0.0:
        incr = incremental_runoff_static(hyeto, cn0); strace = None
    else:
        incr, strace = incremental_runoff_dynamic(hyeto, cn0, cn_sat, beta, drain_in_hr, dt_hr)
    qp = peak_from_incr(incr, e["DA"], e["Tc"]/60.0, dt_hr=dt_hr)
    cq = calibrate_peak(qp, bid)
    rp = rp_from_q(cq, bid)
    res = dict(basin=bid, cn0=cn0, cn_sat=cn_sat, model_q=qp, calib_q=cq, rp=rp,
               runoff_in=sum(incr), rain_in=sum(hyeto))
    if bid == "CC-WCU-2260":
        d = campus_stage_ft(cq)
        res.update(stage_ft=d, posture=campus_posture(d))
    else:
        res.update(stage_ft=None, posture=posture_from_rp(rp, bid))
    if strace:
        res["cn_end"] = 1000.0/(strace[-1]+10.0)
        res["s_start"] = s_from_cn(cn0); res["s_end"] = strace[-1]
    return res


def load_helene(path="data/k24a_helene_hourly.csv", col="p_in_raw"):
    import csv
    with open(path) as f:
        rows = list(csv.DictReader(f))
    return [float(r[col]) for r in rows]
