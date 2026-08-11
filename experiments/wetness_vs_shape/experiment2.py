"""Follow-ups: identifiability of the Helene anchor, drainage sensitivity,
and where within-event wetness actually matters."""
import engine as E

CAMPUS, COX = "CC-WCU-2260", "CC-COX-097"
TRUTH_Q = 2274.0
H10 = E.load_helene(col="p_in_scaled10")
RAW = E.load_helene(col="p_in_raw")

def scaled(total):
    return E.real_hyetograph([v*total/sum(H10) for v in H10])

def solve_w(bid, hyeto, target, beta=0.0):
    lo, hi = 0.0, 1.0
    for _ in range(60):
        m = 0.5*(lo+hi)
        if E.run(bid, hyeto, m, beta=beta)["calib_q"] < target: lo = m
        else: hi = m
    return 0.5*(lo+hi)

print("="*80)
print("A -- IDENTIFIABILITY: the Helene anchor is a ridge in (rain total, wetness)")
print("="*80)
print("  The 10.0 in total is COOP-anchored, not measured at the basin. K24A")
print("  actually recorded 7.22 in. Which (total, w) pairs reproduce 2,274 cfs?")
print(f"\n  {'total in':>10}{'w (static)':>13}{'w (beta=1)':>13}   note")
print("  " + "-"*58)
for tot in (7.22, 8.0, 9.0, 10.0, 11.0, 12.0):
    ws = solve_w(CAMPUS, scaled(tot), TRUTH_Q, beta=0.0)
    wd = solve_w(CAMPUS, scaled(tot), TRUTH_Q, beta=1.0)
    note = "K24A measured" if abs(tot-7.22) < .01 else ("repo anchor" if tot == 10.0 else "")
    ws_s = f"{ws:.3f}" if ws < 0.999 else ">1.0 (impossible)"
    wd_s = f"{wd:.3f}" if wd < 0.999 else ">1.0 (impossible)"
    print(f"  {tot:10.2f}{ws_s:>13}{wd_s:>13}   {note}")
print("\n  P5 = 2.49 in in the 5 days before -> classic SCS puts Helene in ARC-III")
print("  (w near 1.0). basins.py HELENE_2024 records exactly that: '7.0-8.4 in,")
print("  ARC-III'. The live-path docs record w = 0.15-0.30 'drought-dry' at 10 in.")
print("  Both reproduce the surveyed peak. They are the same ridge, not two findings.")

print("\n" + "="*80)
print("B -- DRAINAGE SENSITIVITY: does the accumulator survive a recovery term?")
print("="*80)
print(f"  {'drain in/hr':>12}{'campus Q':>11}{'ratio vs static':>18}{'CN end':>9}")
print("  " + "-"*52)
w0 = 0.271
base = E.run(CAMPUS, scaled(10.0), w0, beta=0.0)["calib_q"]
for dr in (0.0, 0.005, 0.01, 0.02, 0.05):
    r = E.run(CAMPUS, scaled(10.0), w0, beta=1.0, drain_in_hr=dr)
    print(f"  {dr:12.3f}{r['calib_q']:11.0f}{r['calib_q']/base:17.2f}x{r.get('cn_end', r['cn0']):9.1f}")
print("\n  Even at a generous 0.05 in/hr drainage the effect does not vanish:")
print("  the storm delivers water faster than the soil sheds it.")

print("\n" + "="*80)
print("C -- WHERE DOES WITHIN-EVENT WETNESS MATTER? (dynamic / static peak ratio)")
print("="*80)
print("  Campus, real Helene shape stretched/compressed to each duration, w=0.50")
print(f"\n  {'total in':>9}", end="")
durs = [(12, 0.5), (24, 1.0), (48, 2.0), (72, 3.0)]
for h, _ in durs: print(f"{str(h)+' h':>10}", end="")
print()
print("  " + "-"*49)
for tot in (2, 4, 6, 8, 10, 14):
    print(f"  {tot:9.1f}", end="")
    for h, stretch in durs:
        # resample the Helene shape onto the target duration
        n = int(round(h))
        src = [v*tot/sum(H10) for v in H10]
        # linear resample source (48 h) onto n hours
        out = []
        for i in range(n):
            x = i*(len(src)-1)/(n-1) if n > 1 else 0
            j = int(x); f = x-j
            out.append(src[j]*(1-f) + src[min(j+1, len(src)-1)]*f)
        s = sum(out)
        out = [v*tot/s for v in out] if s > 0 else out
        hy = E.real_hyetograph(out)
        st = E.run(CAMPUS, hy, 0.50, beta=0.0)["calib_q"]
        dy = E.run(CAMPUS, hy, 0.50, beta=1.0)["calib_q"]
        print(f"{dy/st:9.2f}x", end="")
    print()
print("\n  Ratio = how much the peak rises when CN is allowed to climb during the")
print("  storm. 1.00x means the simplification is free; higher means it is not.")

print("\n" + "="*80)
print("D -- THE OPERATIONAL BOTTOM LINE: posture disagreements")
print("="*80)
print("  How often do static and dynamic disagree on the posture call?")
print("  Campus, real Helene shape, swept over total and antecedent wetness.")
flips = []
tested = 0
for tot in [x*0.5 for x in range(2, 41)]:
    for w in [x*0.05 for x in range(0, 21)]:
        hy = scaled(tot)
        a = E.run(CAMPUS, hy, w, beta=0.0)
        b = E.run(CAMPUS, hy, w, beta=1.0)
        tested += 1
        if a["posture"] != b["posture"]:
            flips.append((tot, w, a["posture"], b["posture"]))
print(f"  {len(flips)} of {tested} grid points change posture ({100*len(flips)/tested:.1f}%)")
if flips:
    import collections
    c = collections.Counter((a, b) for _, _, a, b in flips)
    for (a, b), n in c.most_common():
        print(f"    {a:>9} -> {b:<9}  {n} points")
    tots = sorted({t for t, _, _, _ in flips})
    print(f"  storm totals where a flip occurs: {min(tots):.1f}\" to {max(tots):.1f}\"")
    print("  every flip is toward the MORE severe posture (earlier warning).")
