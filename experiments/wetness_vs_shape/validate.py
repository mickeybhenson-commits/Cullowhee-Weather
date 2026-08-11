"""Validate the port against every documented anchor before trusting anything new."""
import engine as E

H_RAW = E.load_helene(col="p_in_raw")
H_10  = E.load_helene(col="p_in_scaled10")

print("="*78)
print("PORT VALIDATION")
print("="*78)
print(f"Helene K24A hourly: {len(H_RAW)} h, raw total {sum(H_RAW):.2f} in, "
      f"peak {max(H_RAW):.2f} in/hr")
print(f"scaled-to-10 total {sum(H_10):.2f} in, peak {max(H_10):.2f} in/hr")

# --- A. beta=0 must reproduce the static path EXACTLY -------------------------
hy = E.real_hyetograph(H_10)
cn0 = E.cn_from_wetness(64, 0.15); cn_sat = E.cn_arc3(64)
a = E.incremental_runoff_static(hy, cn0)
b, _ = E.incremental_runoff_dynamic(hy, cn0, cn_sat, beta=0.0)
maxdiff = max(abs(x-y) for x, y in zip(a, b))
print(f"\n[A] beta=0 == static path:  max |diff| = {maxdiff:.3e}   "
      f"{'PASS' if maxdiff < 1e-12 else 'FAIL'}")

# --- B. cnFromWetness(w=1) == ARC-III, per the published worked example -------
print("\n[B] cn_from_wetness(CN2, w=1.0) vs published ARC-III worked example")
pub = {"CC-COX-097": 82.0, "CC-LB-171": 81.3, "CC-MS-1100": 80.0,
       "CC-TIL-705": 80.0, "CC-SPD-1830": 80.0, "CC-WCU-2260": 80.6}
ok = True
for bid, want in pub.items():
    got = E.cn_from_wetness(E.ENGINE[bid]["CN2"], 1.0)
    flag = abs(got-want) < 0.06
    ok &= flag
    print(f"    {bid:14s} got {got:6.2f}  published {want:5.1f}  {'ok' if flag else 'MISMATCH'}")
print(f"    -> {'PASS' if ok else 'FAIL'}")

# --- C. Helene real shape @ 10 in, campus: documented 2130 cfs / 8 yr (w=.15),
#        2345 / 10 yr (w=.30); surveyed truth 2274 cfs / ~9 yr / ~8.4 ft --------
print("\n[C] Campus, Helene real 48-h shape @ 10.0 in, static wetness")
print(f"    {'w':>6}{'model Q':>10}{'calib Q':>10}{'RP yr':>8}{'stage ft':>10}   documented")
doc = {0.15: "2,130 cfs / 8 yr", 0.30: "2,345 cfs / 10 yr", 0.50: "2,578 cfs / 14 yr",
       0.90: "2,847 cfs / 19 yr", 1.00: "2,894 cfs / 20 yr"}
for w in (0.15, 0.30, 0.50, 0.90, 1.00):
    r = E.run("CC-WCU-2260", hy, w)
    print(f"    {w:6.2f}{r['model_q']:10.0f}{r['calib_q']:10.0f}{r['rp']:8.1f}"
          f"{r['stage_ft']:10.2f}   {doc[w]}")
print("    surveyed-mark truth: 2,274 cfs, ~9 yr, campus ~8.4 ft")

# --- D. Type II @ 10 in, drought-dry: documented 5401 cfs / 181 yr / 11.2 ft ---
print("\n[D] Campus, SCS Type II 24-h @ 10.0 in, static wetness")
t2 = E.type2_hyetograph(10.0)
for w in (0.15, 0.30, 0.50):
    r = E.run("CC-WCU-2260", t2, w)
    print(f"    w={w:.2f}  model {r['model_q']:8.0f}  calib {r['calib_q']:8.0f}  "
          f"RP {r['rp']:6.1f}  stage {r['stage_ft']:5.2f} ft  {r['posture']}")
print("    documented (drought-dry): 5,401 cfs, 181 yr, 11.2 ft, EMERGENCY")

# --- E. worked example: 3.0 in, saturated ------------------------------------
print("\n[E] Worked example: 3.0 in, w=1.0  (published: raw 4784, calib 2308, RP 9.5, 8.43 ft)")
for label, h in (("Type II 24h", E.type2_hyetograph(3.0)),
                 ("uniform 24h", E.uniform_hyetograph(3.0, 24))):
    r = E.run("CC-WCU-2260", h, 1.0)
    print(f"    {label:12s} raw {r['model_q']:8.0f}  calib {r['calib_q']:8.0f}  "
          f"RP {r['rp']:5.1f}  stage {r['stage_ft']:5.2f} ft")

# --- F. runoff ratio at the Helene anchor ------------------------------------
print("\n[F] Runoff ratio (documented 0.41 at the Helene forcing)")
for w in (0.15, 0.30, 0.50):
    r = E.run("CC-WCU-2260", hy, w)
    print(f"    w={w:.2f}  rain {r['rain_in']:.2f} in  runoff {r['runoff_in']:.2f} in  "
          f"ratio {r['runoff_in']/r['rain_in']:.3f}")
