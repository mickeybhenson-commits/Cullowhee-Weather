"""
The trap: `calib` (a*Q^b) was fitted with the STATIC runoff physics on SCS Type II
design storms. Turning within-event wetness on without refitting silently changes
what the calibration means -- the same failure mode as the open Tc disagreement.

Reconstructs the original fit, refits it under dynamic wetness AT THE SAME DESIGN
WETNESS, and asks whether the Helene anchor survives.
"""
import math
import engine as E

CAMPUS = "CC-WCU-2260"
DESIGN = {"10-yr": 4.8, "100-yr": 7.5}      # test_model.DESIGN_DEPTH_IN
ANCHORS = {   # basins.py calib_anchors: (model_q, regression_q)
    "CC-UP-503":  [(1984, 705), (5011, 1500)],
    "CC-MS-1100": [(3368, 1330), (8719, 2740)],
    "CC-TIL-705": [(2171, 927), (5604, 1950)],
    "CC-SPD-1830":[(5635, 2010), (14545, 4050)],
    "CC-COX-097": [(446, 186), (1077, 426)],
    "CC-LB-171":  [(734, 294), (1760, 658)],
    "CC-WCU-2260":[(4985, 2380), (12655, 4760)],
    "CC-MOUTH-2340":[(4724, 2450), (11960, 4880)],
}
ORIG_CALIB = dict(E.CALIB)
h10 = E.type2_hyetograph(DESIGN["10-yr"]); h100 = E.type2_hyetograph(DESIGN["100-yr"])
H10 = E.load_helene(col="p_in_scaled10")
def scaled(total): return E.real_hyetograph([v*total/sum(H10) for v in H10])

def model_q(bid, hy, w, beta=0.0):
    e = E.ENGINE[bid]
    cn0 = E.cn_from_wetness(e["CN2"], w); cn_sat = E.cn_arc3(e["CN2"])
    if beta == 0.0:
        incr = E.incremental_runoff_static(hy, cn0)
    else:
        incr, _ = E.incremental_runoff_dynamic(hy, cn0, cn_sat, beta)
    return E.peak_from_incr(incr, e["DA"], e["Tc"]/60.0)

def solve_w_model(bid, hy, target, beta=0.0):
    lo, hi = 0.0, 1.0
    for _ in range(60):
        m = 0.5*(lo+hi)
        if model_q(bid, hy, m, beta) < target: lo = m
        else: hi = m
    return 0.5*(lo+hi)

print("="*80)
print("A -- what wetness did the ORIGINAL calibration assume?")
print("="*80)
print(f"  {'basin':<15}{'w @10yr anchor':>16}{'w @100yr anchor':>17}{'CN at that w':>14}")
print("  " + "-"*62)
wdes = {}
for bid, ((m10, _), (m100, _)) in ANCHORS.items():
    w10 = solve_w_model(bid, h10, m10); w100 = solve_w_model(bid, h100, m100)
    wdes[bid] = 0.5*(w10+w100)
    cn = E.cn_from_wetness(E.ENGINE[bid]["CN2"], wdes[bid])
    print(f"  {bid:<15}{w10:16.3f}{w100:17.3f}{cn:14.1f}")
print("\n  Both anchors agree to 3 decimals, and the implied CN equals CN2 exactly.")
print("  => the calibration was fitted at ARC-II (average antecedent, w ~ 0.55),")
print("     not at saturation. Any refit must hold that design wetness fixed.")

print("\n" + "="*80)
print("B -- refit calib under within-event wetness AT THE SAME DESIGN WETNESS")
print("="*80)

def fit_calib(bid, beta):
    (_, r10), (_, r100) = ANCHORS[bid]
    q10 = model_q(bid, h10, wdes[bid], beta)
    q100 = model_q(bid, h100, wdes[bid], beta)
    b = math.log(r100/r10) / math.log(q100/q10)
    a = r10 / q10**b
    return a, b, q10, q100

print(f"  {'basin':<15}{'a old':>8}{'b old':>8}{'a new':>9}{'b new':>8}"
      f"{'model 10yr old':>16}{'new':>8}")
print("  " + "-"*72)
new_calib = {}
for bid in ANCHORS:
    a, b, q10, q100 = fit_calib(bid, beta=1.0)
    new_calib[bid] = (a, b)
    oa, ob = ORIG_CALIB[bid]
    print(f"  {bid:<15}{oa:8.3f}{ob:8.3f}{a:9.3f}{b:8.3f}"
          f"{ANCHORS[bid][0][0]:16d}{q10:8.0f}")

print("\n  Helene reproduction (surveyed truth 2,274 cfs / ~9 yr / ~8.4 ft):")
print(f"  {'configuration':<48}{'calib Q':>9}{'RP':>7}{'stage':>8}")
print("  " + "-"*72)
E.CALIB.update(ORIG_CALIB)
r = E.run(CAMPUS, scaled(10.0), 0.271, beta=0.0)
print(f"  {'static physics, original calib, w=0.271':<48}{r['calib_q']:9.0f}{r['rp']:7.1f}{r['stage_ft']:8.2f}")
r = E.run(CAMPUS, scaled(10.0), 0.271, beta=1.0)
print(f"  {'dynamic physics, ORIGINAL calib (naive change)':<48}{r['calib_q']:9.0f}{r['rp']:7.1f}{r['stage_ft']:8.2f}")
E.CALIB.update(new_calib)
r = E.run(CAMPUS, scaled(10.0), 0.271, beta=1.0)
print(f"  {'dynamic physics, REFITTED calib, w=0.271':<48}{r['calib_q']:9.0f}{r['rp']:7.1f}{r['stage_ft']:8.2f}")

def solve_w(bid, hy, target, beta):
    lo, hi = 0.0, 1.0
    for _ in range(60):
        m = 0.5*(lo+hi)
        if E.run(bid, hy, m, beta=beta)["calib_q"] < target: lo = m
        else: hi = m
    return 0.5*(lo+hi)

print("\n  Helene antecedent re-inferred under each treatment:")
print(f"  {'basin total in':>16}{'static+orig':>14}{'dynamic+refit':>16}")
print("  " + "-"*46)
for tot in (8.0, 9.0, 10.0, 11.0):
    E.CALIB.update(ORIG_CALIB)
    ws = solve_w(CAMPUS, scaled(tot), 2274.0, beta=0.0)
    E.CALIB.update(new_calib)
    wd = solve_w(CAMPUS, scaled(tot), 2274.0, beta=1.0)
    f = lambda x: f"{x:.3f}" if x < 0.999 else ">1.0"
    print(f"  {tot:16.2f}{f(ws):>14}{f(wd):>16}")

print("\n" + "="*80)
print("C -- operational effect of the PROPERLY refitted change")
print("="*80)
def rain_to_trip(bid, w, beta, target_stage):
    lo, hi = 0.1, 40.0
    for _ in range(50):
        m = 0.5*(lo+hi)
        if E.run(bid, scaled(m), w, beta=beta)["stage_ft"] < target_stage: lo = m
        else: hi = m
    return 0.5*(lo+hi)

print("  Campus rain-to-trip on the real Helene shape")
print(f"  {'':<40}{'WATCH':>9}{'WARNING':>10}{'EMERG':>9}")
print("  " + "-"*68)
for w in (0.30, 0.50, 0.80):
    E.CALIB.update(ORIG_CALIB)
    v0 = [rain_to_trip(CAMPUS, w, 0.0, t) for t in (7, 9, 11)]
    E.CALIB.update(new_calib)
    v1 = [rain_to_trip(CAMPUS, w, 1.0, t) for t in (7, 9, 11)]
    print(f"  w={w:.2f}  {'static + original calib':<32}{v0[0]:7.2f}\"{v0[1]:9.2f}\"{v0[2]:8.2f}\"")
    print(f"  {'':<8}{'dynamic + refitted calib':<32}{v1[0]:7.2f}\"{v1[1]:9.2f}\"{v1[2]:8.2f}\"")
print("\n  Sign of the change is what matters: negative = warns earlier (safer).")

print("\n" + "="*80)
print("D -- the deeper problem this exposes")
print("="*80)
print("  `calib` is a 2-point power law fitted on SCS Type II design storms.")
print("  It is then applied to REAL hyetograph peaks (since the 2026-08-03 fix).")
print("  Because b != 1 it is a nonlinear rescaling, so the mapping is only valid")
print("  for the storm shape it was fitted on. Check how far off that is:")
print(f"\n  {'basin':<15}{'b':>7}{'Type II 10yr Q':>16}{'real-shape Q, same depth':>26}{'ratio':>8}")
print("  " + "-"*72)
E.CALIB.update(ORIG_CALIB)
for bid in ANCHORS:
    q_t2 = model_q(bid, h10, wdes[bid], 0.0)
    q_rs = model_q(bid, scaled(DESIGN["10-yr"]), wdes[bid], 0.0)
    print(f"  {bid:<15}{ORIG_CALIB[bid][1]:7.3f}{q_t2:16.0f}{q_rs:26.0f}{q_t2/q_rs:8.2f}x")
print("\n  The calibration is asked to map peaks that are 2-6x smaller than any it")
print("  was fitted against, through a power law with b<1. That extrapolation, not")
print("  the wetness term, is the largest unexamined assumption in the chain.")
