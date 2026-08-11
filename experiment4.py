"""What the calibration's design basis is worth: refit on the real-shape path."""
import math, engine as E

DESIGN = {"10-yr": 4.8, "100-yr": 7.5}
ANCHORS = {
    "CC-UP-503":[(1984,705),(5011,1500)], "CC-MS-1100":[(3368,1330),(8719,2740)],
    "CC-TIL-705":[(2171,927),(5604,1950)], "CC-SPD-1830":[(5635,2010),(14545,4050)],
    "CC-COX-097":[(446,186),(1077,426)], "CC-LB-171":[(734,294),(1760,658)],
    "CC-WCU-2260":[(4985,2380),(12655,4760)], "CC-MOUTH-2340":[(4724,2450),(11960,4880)],
}
ORIG = dict(E.CALIB)
H10 = E.load_helene(col="p_in_scaled10")
def scaled(t): return E.real_hyetograph([v*t/sum(H10) for v in H10])
WDES = {b: 0.556 if b=="CC-COX-097" else 0.553 if b=="CC-LB-171"
        else 0.548 if b in ("CC-WCU-2260","CC-MOUTH-2340") else 0.544 for b in ANCHORS}

def model_q(bid, hy, w, beta=0.0):
    e = E.ENGINE[bid]
    cn0 = E.cn_from_wetness(e["CN2"], w)
    if beta == 0.0: incr = E.incremental_runoff_static(hy, cn0)
    else: incr, _ = E.incremental_runoff_dynamic(hy, cn0, E.cn_arc3(e["CN2"]), beta)
    return E.peak_from_incr(incr, e["DA"], e["Tc"]/60.0)

print("="*78)
print("WHAT DOES THE MODEL SAY A 10-YR RAINFALL PRODUCES?")
print("="*78)
print("  calib maps Type II peaks onto the regression curve. Feed it a REAL-shape")
print("  storm of the same 10-yr rainfall depth (4.8 in) and read the answer:")
print(f"\n  {'basin':<15}{'real-shape Q':>14}{'calib Q':>10}{'implied RP':>12}   design intent")
print("  " + "-"*70)
for bid in ANCHORS:
    q = model_q(bid, scaled(DESIGN["10-yr"]), WDES[bid])
    cq = E.calibrate_peak(q, bid)
    rp = E.rp_from_q(cq, bid)
    print(f"  {bid:<15}{q:14.0f}{cq:10.0f}{rp:12.1f}   10.0 yr")
print("\n  A 10-yr RAINFALL delivered in a realistic 48-h pattern produces a")
print("  1.5-2.5 yr FLOW in this model. That is not necessarily wrong -- Helene")
print("  itself was ~200-yr rain and a ~9-yr peak -- but it means the calibration's")
print("  '10-yr rain = 10-yr flow' design premise does not hold on the live path.")

print("\n" + "="*78)
print("IF calib WERE REFITTED ON THE REAL-SHAPE PATH")
print("="*78)
def fit(bid, hy10, hy100, beta):
    (_, r10), (_, r100) = ANCHORS[bid]
    q10 = model_q(bid, hy10, WDES[bid], beta); q100 = model_q(bid, hy100, WDES[bid], beta)
    b = math.log(r100/r10)/math.log(q100/q10)
    return r10/q10**b, b
rs = {}
print(f"  {'basin':<15}{'a (Type II)':>12}{'b':>7}{'a (real shape)':>16}{'b':>7}")
print("  " + "-"*57)
for bid in ANCHORS:
    a, b = fit(bid, scaled(DESIGN["10-yr"]), scaled(DESIGN["100-yr"]), 0.0)
    rs[bid] = (a, b)
    print(f"  {bid:<15}{ORIG[bid][0]:12.3f}{ORIG[bid][1]:7.3f}{a:16.3f}{b:7.3f}")

print("\n  Helene under each calibration basis (truth 2,274 cfs / ~9 yr / ~8.4 ft):")
print(f"  {'calibration basis':<34}{'w=0.27':>10}{'w=0.55':>10}{'w=0.80':>10}{'w=1.0':>9}")
print("  " + "-"*73)
for label, cal in (("Type II design storms (current)", ORIG), ("real-shape storms", rs)):
    E.CALIB.update(cal)
    row = [E.run("CC-WCU-2260", scaled(10.0), w)["calib_q"] for w in (0.27, 0.55, 0.80, 1.0)]
    print(f"  {label:<34}" + "".join(f"{v:10.0f}" for v in row))
print("  " + " "*34 + f"{'truth ->':>10}{'2274 cfs at some w':>40}")

E.CALIB.update(ORIG)
print("\n" + "="*78)
print("FINAL CHECK -- port still reproduces every published anchor")
print("="*78)
checks = [
    ("campus Helene real 10in w=0.15 -> ~2130 cfs",
     E.run("CC-WCU-2260", scaled(10.0), 0.15)["calib_q"], 2130, 40),
    ("campus Helene real 10in w=1.00 -> ~2894 cfs",
     E.run("CC-WCU-2260", scaled(10.0), 1.00)["calib_q"], 2894, 40),
    ("campus Type II 3.0in w=1.0 -> raw 4784",
     E.run("CC-WCU-2260", E.type2_hyetograph(3.0), 1.0)["model_q"], 4784, 15),
    ("campus Type II 3.0in w=1.0 -> calib 2308",
     E.run("CC-WCU-2260", E.type2_hyetograph(3.0), 1.0)["calib_q"], 2308, 10),
    ("runoff ratio at Helene anchor -> 0.41",
     E.run("CC-WCU-2260", scaled(10.0), 0.271)["runoff_in"]/10.0, 0.41, 0.01),
]
allok = True
for name, got, want, tol in checks:
    ok = abs(got-want) <= tol; allok &= ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<46} got {got:9.2f}  want {want}")
print(f"\n  {'ALL ANCHORS REPRODUCED' if allok else 'SOME ANCHORS FAILED'}")
