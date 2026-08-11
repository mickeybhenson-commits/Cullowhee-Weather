"""Fresh-process re-derivation of every headline number in the findings doc."""
import engine as E
H10 = E.load_helene(col="p_in_scaled10")
RAW = E.load_helene(col="p_in_raw")
def scaled(t): return E.real_hyetograph([v*t/sum(H10) for v in H10])
CAMPUS, COX, TRUTH = "CC-WCU-2260", "CC-COX-097", 2274.0

def solve_w(bid, hy, target, beta=0.0):
    lo, hi = 0.0, 1.0
    for _ in range(60):
        m = .5*(lo+hi)
        if E.run(bid, hy, m, beta=beta)["calib_q"] < target: lo = m
        else: hi = m
    return .5*(lo+hi)

w = solve_w(CAMPUS, scaled(10.0), TRUTH)
print(f"anchor wetness                       w = {w:.3f}")
out = []
for bid, name in ((CAMPUS, "campus"), (COX, "cox")):
    base = E.run(bid, scaled(10.0), w, beta=0.0)["calib_q"]
    t2   = E.run(bid, E.type2_hyetograph(10.0), w, beta=0.0)["calib_q"]
    dyn  = E.run(bid, scaled(10.0), w, beta=1.0)["calib_q"]
    shape, wet = t2/base, dyn/base
    out.append((name, shape, wet, abs(shape-1)/abs(wet-1)))
    print(f"{name:8s} shape {shape:5.2f}x   wetness {wet:5.2f}x   "
          f"ratio {abs(shape-1)/abs(wet-1):5.1f}x")

print(f"\n7.22in (K24A measured) max achievable peak at w=1.0: "
      f"{E.run(CAMPUS, E.real_hyetograph(RAW), 1.0)['calib_q']:.0f} cfs vs truth {TRUTH:.0f}")
lo, hi = 7.0, 12.0
for _ in range(50):
    m = .5*(lo+hi)
    if E.run(CAMPUS, scaled(m), 1.0)["calib_q"] < TRUTH: lo = m
    else: hi = m
print(f"minimum basin rainfall that can reach truth (at w=1.0): {.5*(lo+hi):.2f} in")

print(f"\nridge check: (8.0in, w={solve_w(CAMPUS, scaled(8.0), TRUTH):.3f}) and "
      f"(10.0in, w={solve_w(CAMPUS, scaled(10.0), TRUTH):.3f}) both -> {TRUTH:.0f} cfs")
for tot, ww in ((8.0, solve_w(CAMPUS, scaled(8.0), TRUTH)), (10.0, w)):
    print(f"   verify {tot:.1f}in @ w={ww:.3f} -> {E.run(CAMPUS, scaled(tot), ww)['calib_q']:.0f} cfs")

print("\nimplied RP of a 10-yr rainfall (4.8in) on the real-shape path:")
for bid in ("CC-COX-097", "CC-WCU-2260", "CC-MOUTH-2340"):
    wd = 0.556 if bid == COX else 0.548
    r = E.run(bid, scaled(4.8), wd)
    print(f"   {bid:15s} RP {r['rp']:.1f} yr")
