"""
The 2x2: {SCS Type II 24h, real Helene 48h} x {static wetness, within-event wetness}
measured against the surveyed-mark truth.

Truth (HELENE_RETURN_PERIOD_CONFLICT.md + noah_helene_calibration.md):
  campus peak 2,274 cfs, ~9 yr, campus stage ~8.4 ft, runoff ratio 0.41
"""
import engine as E

TRUTH_Q = 2274.0
TRUTH_STAGE = 8.4
H_RAW = E.load_helene(col="p_in_raw")     # 7.22 in over 48 h, the actual gauge record
H_10  = E.load_helene(col="p_in_scaled10")
HY_REAL = E.real_hyetograph(H_10)
HY_T2   = E.type2_hyetograph(10.0)
CAMPUS, COX = "CC-WCU-2260", "CC-COX-097"


def solve_w(bid, hyeto, target_q, beta=0.0, lo=0.0, hi=1.0):
    """Bisect for the initial wetness that reproduces target_q."""
    for _ in range(60):
        m = 0.5*(lo+hi)
        if E.run(bid, hyeto, m, beta=beta)["calib_q"] < target_q: lo = m
        else: hi = m
    return 0.5*(lo+hi)


print("="*80)
print("STEP 1 -- pin the Helene anchor: which initial wetness reproduces truth?")
print("="*80)
w_anchor = solve_w(CAMPUS, HY_REAL, TRUTH_Q)
r = E.run(CAMPUS, HY_REAL, w_anchor)
print(f"  real 48-h shape @10 in, static wetness -> w = {w_anchor:.3f}")
print(f"  gives calib Q {r['calib_q']:.0f} cfs, RP {r['rp']:.1f} yr, "
      f"stage {r['stage_ft']:.2f} ft, runoff ratio {r['runoff_in']/r['rain_in']:.3f}")
print(f"  (docs label this 'drought-dry'; the published 0.41 runoff ratio and the")
print(f"   5,401 cfs Type II figure both land near w=0.28-0.30, not w=0.15)")

print("\n" + "="*80)
print("STEP 2 -- the 2x2, campus, Helene forcing (10.0 in), anchor wetness")
print("="*80)
hdr = f"  {'configuration':<34}{'calib Q':>9}{'RP yr':>8}{'stage':>8}{'posture':>11}{'vs truth':>10}"
print(hdr); print("  " + "-"*(len(hdr)-2))
cells = {}
for sname, hy in (("SCS Type II 24 h", HY_T2), ("real Helene 48 h", HY_REAL)):
    for wname, beta in (("static wetness", 0.0), ("within-event wetness", 1.0)):
        res = E.run(CAMPUS, hy, w_anchor, beta=beta)
        cells[(sname, wname)] = res
        print(f"  {sname + ' + ' + wname:<34}{res['calib_q']:9.0f}{res['rp']:8.1f}"
              f"{res['stage_ft']:8.2f}{res['posture']:>11}{res['calib_q']/TRUTH_Q:9.2f}x")
print(f"  {'SURVEYED TRUTH':<34}{TRUTH_Q:9.0f}{9.0:8.1f}{TRUTH_STAGE:8.2f}{'WATCH':>11}{1.00:9.2f}x")

print("\n  Decomposition:")
base = cells[("real Helene 48 h", "static wetness")]["calib_q"]
shape_err = cells[("SCS Type II 24 h", "static wetness")]["calib_q"] / base
wet_err   = cells[("real Helene 48 h", "within-event wetness")]["calib_q"] / base
both      = cells[("SCS Type II 24 h", "within-event wetness")]["calib_q"] / base
print(f"    shape error alone (Type II vs real, static w)      {shape_err:6.2f}x  "
      f"({'over' if shape_err>1 else 'under'}-predicts)")
print(f"    frozen-wetness error alone (real shape, static->dyn){wet_err:6.2f}x  "
      f"({'over' if wet_err>1 else 'under'}-predicts)")
print(f"    both together                                      {both:6.2f}x")
print(f"    -> shape error is {abs(shape_err-1)/max(abs(wet_err-1),1e-9):.1f}x "
      f"the size of the frozen-wetness error")

print("\n" + "="*80)
print("STEP 3 -- same 2x2 on the flashiest reach (Cox Branch, Tc 29 min)")
print("="*80)
print(f"  {'configuration':<34}{'calib Q':>9}{'RP yr':>8}{'posture':>11}")
print("  " + "-"*62)
cox = {}
for sname, hy in (("SCS Type II 24 h", HY_T2), ("real Helene 48 h", HY_REAL)):
    for wname, beta in (("static wetness", 0.0), ("within-event wetness", 1.0)):
        res = E.run(COX, hy, w_anchor, beta=beta)
        cox[(sname, wname)] = res
        print(f"  {sname + ' + ' + wname:<34}{res['calib_q']:9.0f}{res['rp']:8.1f}{res['posture']:>11}")
cbase = cox[("real Helene 48 h", "static wetness")]["calib_q"]
print(f"\n    shape error       {cox[('SCS Type II 24 h','static wetness')]['calib_q']/cbase:6.2f}x")
print(f"    frozen-wetness    {cox[('real Helene 48 h','within-event wetness')]['calib_q']/cbase:6.2f}x")

print("\n" + "="*80)
print("STEP 4 -- beta sensitivity (beta = fraction of infiltration that consumes storage)")
print("="*80)
print(f"  {'beta':>6}{'campus Q':>11}{'RP':>7}{'stage':>8}{'CN start':>10}{'CN end':>9}"
      f"{'re-inferred w':>15}")
print("  " + "-"*66)
for beta in (0.0, 0.25, 0.5, 0.75, 1.0):
    res = E.run(CAMPUS, HY_REAL, w_anchor, beta=beta)
    w_re = solve_w(CAMPUS, HY_REAL, TRUTH_Q, beta=beta)
    cn_end = res.get("cn_end", res["cn0"])
    print(f"  {beta:6.2f}{res['calib_q']:11.0f}{res['rp']:7.1f}{res['stage_ft']:8.2f}"
          f"{res['cn0']:10.1f}{cn_end:9.1f}{w_re:15.3f}")
print("\n  're-inferred w' = the antecedent wetness Helene implies IF the model")
print("  carries within-event accounting. The calibration anchor moves with beta.")

print("\n" + "="*80)
print("STEP 5 -- does it matter operationally? rain-to-trip, campus, w=0.50")
print("="*80)
def rain_to_trip(bid, shape, w, beta, target, hours=48):
    lo, hi = 0.1, 40.0
    for _ in range(50):
        m = 0.5*(lo+hi)
        if shape == "real":
            hy = E.real_hyetograph(E.load_helene(col="p_in_scaled10"))
            hy = [v*m/10.0 for v in hy]
        else:
            hy = E.type2_hyetograph(m)
        res = E.run(bid, hy, w, beta=beta)
        val = res["stage_ft"] if bid == CAMPUS else res["rp"]
        if val < target: lo = m
        else: hi = m
    return 0.5*(lo+hi)

print(f"  {'':<26}{'WATCH 7ft':>12}{'WARNING 9ft':>13}{'EMERGENCY 11ft':>16}")
print("  " + "-"*67)
for label, shape, beta in (("Type II + static", "t2", 0.0),
                           ("Helene shape + static", "real", 0.0),
                           ("Helene shape + dynamic", "real", 1.0)):
    vals = [rain_to_trip(CAMPUS, shape, 0.50, beta, t) for t in (7.0, 9.0, 11.0)]
    print(f"  {label:<26}{vals[0]:11.2f}\"{vals[1]:12.2f}\"{vals[2]:15.2f}\"")
