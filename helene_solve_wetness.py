#!/usr/bin/env python3
"""
helene_solve_wetness.py — with rainfall measured, what antecedent wetness does
Helene actually imply?

Run helene_mrms_reconstruct.py first; this consumes its CSV.

WHAT IT ANSWERS
---------------
1. The wetness w that reproduces the surveyed campus peak (2,274 cfs) given the
   MEASURED hourly hyetograph — not an assumed 10-inch total, and not an assumed
   shape either. This is the number the whole calibration hangs on, and it has never
   been computed from measured rainfall.
2. How much of that answer came from the measured TOTAL and how much from the measured
   SHAPE, by re-solving at the same total under the K24A shape. Shape is not a detail
   here: at 10.00 in, the K24A shape implies w = 0.248, a back-loaded ramp implies
   0.434, and a uniform 48-hour storm is off-ridge entirely — saturated soil cannot
   reach the surveyed peak. A total alone does not collapse the ridge.
3. What every basin's peak, return period and posture would have been under measured
   forcing, versus what the model says using its own single-point forcing.
4. Whether the eight basins actually move together when each is given its own rain.

WHAT THE SOLVED w IS, AND IS NOT  (updated 2026-08-12)
-----------------------------------------------------
It is the single FROZEN wetness that best fits the surveyed peak given measured rain.
It is NOT Helene's antecedent condition, because Helene did not have one.

The measured USCRN trajectory (noah_helene_ridge_is_frozen_wetness_2026-08-12.md) shows
the catchment going from w ~0.5 on 24 Sep to saturated by 26 Sep — before the main
rainfall day. The two records this script adjudicates are both correct about different
days: the permeability note's "drought-dry" describes mid-September, and basins.py's
ARC-III describes 26-27 September. Neither is an error. The model is, for carrying one w
across an event that changed the soil by half its range.

So read the verdict below as "which end of the event does a single frozen parameter land
on when forced with measured rain", and do not restate it as Helene's soil condition.
The CONTRADICTED/CONSISTENT labels are kept because they are still the right check on
whether the repo's fitted anchor is defensible — but a result of MIDDLING is the expected
one, and it means the frozen-w assumption is the thing to fix, not either record.

A CONSTRAINT THAT HOLDS BEFORE ANY RADAR IS READ
------------------------------------------------
Under the K24A shape the hard floor is 7.48 in: below that, no antecedent condition
reproduces the surveyed peak. K24A itself measured 7.22 in. So the basins out-rained the
valley gauge, and any reconstruction returning less than ~7.5 in over the campus basin is
telling you about the radar, not about the soil.

    python helene_solve_wetness.py
    python helene_solve_wetness.py --csv helene_basin_rain.csv --target 2274
    python helene_solve_wetness.py --selftest      # offline, no CSV needed

Deps: cwm_model.py, basins.py (repo root). Stdlib otherwise.
"""

import argparse
import collections
import csv
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cwm_model  # noqa: E402

CAMPUS = "CC-WCU-2260"
SURVEYED_PEAK_CFS = 2274.0
SURVEYED_RP_YR = 9.0
SURVEYED_STAGE_FT = 8.4
K24A_TOTAL_IN = 7.22
FLOOR_IN = 7.48                   # w=1.0 under the K24A shape; see module docstring

# The two records in conflict, for the verdict at the end.
RECORD_WET = ("basins.py HELENE_2024", "7.0-8.4 in, ARC-III (P5 2.49 in)", 0.80, 1.00)
RECORD_DRY = ("noah_permeability_lever_is_wetness_2026-08-10.md",
              "10 in, drought-dry", 0.15, 0.30)

# Gap tolerance. Above this fraction of missing hours the shape is not measured any
# more, it is interpolated, and the whole point of this script is that shape decides
# the answer. Refuse rather than report a number that looks measured.
GAP_REFUSE = 0.10
GAP_WARN = 0.02


def load_series(path):
    """-> {basin_id: [(utc_hour, inches), ...]} sorted by time."""
    by = collections.defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            by[r["basin_id"]].append(
                (dt.datetime.strptime(r["valid_utc"], "%Y-%m-%dT%H:00:00Z"),
                 float(r["qpe_in"])))
    for b in by:
        by[b].sort()
    return dict(by)


def hyeto(series):
    """-> (hourly inches, n_gaps). Gaps are zero-filled but COUNTED, never hidden.

    An absent row means the hour was not retrieved, or the basin's cell coverage fell
    below the gate. It does NOT mean zero rain — the reconstruction writes explicit
    0.0 rows for dry hours. Zero-filling biases the total down (conservative) but
    distorts the shape (not conservative, because shape drives the peak). So the count
    comes back with the series and the caller gates on it.
    """
    if not series:
        return [], 0
    t0, t1 = series[0][0], series[-1][0]
    n = int((t1 - t0).total_seconds() // 3600) + 1
    out = [0.0] * n
    for t, v in series:
        out[int((t - t0).total_seconds() // 3600)] = v
    return out, n - len(series)


def rescale(hourly, total):
    """Same shape, different total."""
    s = sum(hourly)
    return [v * total / s for v in hourly] if s > 0 else list(hourly)


def k24a_shape(n, total):
    """K24A's observed hourly pattern, resampled to n hours at the given total.

    Only used to answer 'how much of the answer was shape?'. If the real K24A series
    is not on disk, fall back to the documented double-peak envelope rather than
    silently comparing against something uniform.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    src = None
    for rel in ("data/k24a_helene_hourly.csv",
                "experiments/wetness_vs_shape/data/k24a_helene_hourly.csv",
                "k24a_helene_hourly.csv"):
        path = os.path.join(here, *rel.split("/"))
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                src = [float(r["p_in_raw"]) for r in csv.DictReader(f)]
            break
    if not src:
        src = [0.02, 0.03, 0.0, 0.01, 0.05, 0.10, 0.18, 0.25, 0.30, 0.22, 0.14, 0.08,
               0.05, 0.03, 0.02, 0.02, 0.03, 0.06, 0.12, 0.24, 0.41, 0.62, 0.78, 0.71,
               0.55, 0.40, 0.28, 0.19, 0.12, 0.08, 0.05, 0.03]
    out = [0.0] * n
    for i, v in enumerate(src):                       # proportional resample
        out[min(n - 1, int(i * n / len(src)))] += v
    return rescale(out, total)


def solve_w(bid, hourly, target, lo=0.0, hi=1.0):
    """Bisect for the wetness reproducing `target` cfs. None if off-ridge."""
    f = lambda w: cwm_model.assess_event(bid, hourly, w)["calib_q"]
    if f(hi) < target:
        return None, "above"          # even saturated soil cannot get there
    if f(lo) > target:
        return None, "below"          # even bone-dry soil overshoots
    for _ in range(60):
        m = 0.5 * (lo + hi)
        if f(m) < target:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi), "ok"


# --------------------------------------------------------------------------- #
def selftest():
    """Offline. Builds a synthetic CSV with a KNOWN answer and checks we recover it."""
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))

    print("helene_solve_wetness — offline self-test")
    print("=" * 78)
    print("engine round-trip (plant a known w, recover it)")
    shape = k24a_shape(48, 10.0)
    for planted in (0.15, 0.248, 0.50, 0.80):
        q = cwm_model.assess_event(CAMPUS, shape, planted)["calib_q"]
        got, st = solve_w(CAMPUS, shape, q)
        chk(f"w={planted:.3f} recovered from its own peak",
            st == "ok" and abs(got - planted) < 1e-3,
            f"got {got if got is None else f'{got:.4f}'} ({st})")

    print("\nthe ridge is real, and it is 2-D")
    w10, _ = solve_w(CAMPUS, shape, SURVEYED_PEAK_CFS)
    w08, _ = solve_w(CAMPUS, k24a_shape(48, 8.0), SURVEYED_PEAK_CFS)
    chk("8 in and 10 in both reach the surveyed peak, at different w",
        w08 is not None and w10 is not None and abs(w08 - w10) > 0.3,
        f"8 in -> w {w08:.3f} | 10 in -> w {w10:.3f}")
    uni, st_uni = solve_w(CAMPUS, [10.0 / 48] * 48, SURVEYED_PEAK_CFS)
    chk("same 10 in spread uniformly is OFF-RIDGE (shape decides)",
        uni is None and st_uni == "above",
        f"uniform 48 h -> {st_uni}")
    chk("K24A's own 7.22 in cannot reach the peak at any wetness",
        solve_w(CAMPUS, k24a_shape(48, K24A_TOTAL_IN), SURVEYED_PEAK_CFS)[1] == "above",
        f"{K24A_TOTAL_IN} in < {FLOOR_IN} in floor")

    print("\ngap accounting — absent hours must not read as dry hours")
    t0 = dt.datetime(2024, 9, 26, 0)
    full = [(t0 + dt.timedelta(hours=i), 0.1) for i in range(10)]
    h, g = hyeto(full)
    chk("complete series reports zero gaps", g == 0 and len(h) == 10)
    h2, g2 = hyeto(full[:4] + full[6:])
    chk("two dropped hours are COUNTED, not silently zero-filled",
        g2 == 2 and len(h2) == 10 and h2[4] == 0.0,
        f"{g2} gaps flagged, total {sum(h2):.2f} in vs {sum(h):.2f} in true")
    chk("refuse threshold is below the warn-only band", GAP_WARN < GAP_REFUSE)

    print("\nCSV round-trip")
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".selftest_rain.csv")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        wtr = csv.DictWriter(f, fieldnames=["valid_utc", "basin_id", "qpe_mm",
                                            "qpe_in", "valid_frac"])
        wtr.writeheader()
        for i, v in enumerate(shape):
            wtr.writerow({"valid_utc": (t0 + dt.timedelta(hours=i)).strftime(
                              "%Y-%m-%dT%H:00:00Z"),
                          "basin_id": CAMPUS, "qpe_mm": round(v * 25.4, 3),
                          "qpe_in": round(v, 4), "valid_frac": 1.0})
    back = load_series(tmp)
    hb, gb = hyeto(back[CAMPUS])
    chk("written and re-read totals agree", abs(sum(hb) - 10.0) < 0.01 and gb == 0,
        f"{sum(hb):.3f} in, {gb} gaps")
    os.remove(tmp)

    print("\nverdict logic")
    chk("the two records do not overlap", RECORD_DRY[3] < RECORD_WET[2])
    chk("a middling w contradicts BOTH",
        not (RECORD_WET[2] <= 0.50 <= RECORD_WET[3])
        and not (RECORD_DRY[2] <= 0.50 <= RECORD_DRY[3]))

    print("\n" + "=" * 78)
    print("SELF-TEST " + ("PASSED" if ok else "FAILED"))
    print("The engine and the solver are verified. Nothing here touches MRMS —")
    print("run helene_mrms_reconstruct.py for the measured rainfall.")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default="helene_basin_rain.csv")
    ap.add_argument("--target", type=float, default=SURVEYED_PEAK_CFS)
    ap.add_argument("--force", action="store_true",
                    help="report even when too many hours are missing (says so loudly)")
    ap.add_argument("--selftest", action="store_true", help="offline checks, no CSV")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not os.path.exists(a.csv):
        sys.exit(f"{a.csv} not found — run helene_mrms_reconstruct.py first")

    series = load_series(a.csv)
    if CAMPUS not in series:
        sys.exit(f"no rows for {CAMPUS} in {a.csv} — cannot say anything about the "
                 f"anchor. Fix mask coverage first.")

    print("=" * 78)
    print("MEASURED FORCING — MRMS basin means")
    print("=" * 78)
    print(f"{'basin':<16}{'hours':>7}{'gaps':>6}{'total in':>10}{'peak in/hr':>12}")
    print("-" * 78)
    H, tot, gaps = {}, {}, {}
    for bid in cwm_model.ORDER:
        if bid not in series:
            print(f"{bid:<16}{'—':>7}{'—':>6}{'NOT OBSERVED':>22}")
            continue
        h, g = hyeto(series[bid])
        H[bid], tot[bid], gaps[bid] = h, sum(h), g
        flag = "  <-- gaps" if g else ""
        print(f"{bid:<16}{len(h):>7}{g:>6}{sum(h):>10.2f}{max(h):>12.2f}{flag}")
    print("-" * 78)
    print(f"K24A valley gauge, same event: {K24A_TOTAL_IN:.2f} in"
          f"   (floor for the surveyed peak: {FLOOR_IN:.2f} in)")

    # ---------------------------------------------------------------- gate
    gfrac = gaps.get(CAMPUS, 0) / max(1, len(H.get(CAMPUS, [1])))
    if gfrac > GAP_WARN:
        print("\n" + "!" * 78)
        print(f"MISSING HOURS: {gaps[CAMPUS]} of {len(H[CAMPUS])} on the campus basin "
              f"({100*gfrac:.0f}%).")
        print("Absent hours are zero-filled to keep the timing, which lowers the total")
        print("and flattens the shape. Shape decides this answer, so a gappy series")
        print("biases w UPWARD — toward 'wet' — for reasons that are not physical.")
        print("!" * 78)
        if gfrac > GAP_REFUSE and not a.force:
            sys.exit(f"\nREFUSING to report a wetness from a series missing "
                     f"{100*gfrac:.0f}% of its hours. Re-run the reconstruction (the "
                     f"archive is often just slow), or pass --force and quote the gap "
                     f"fraction everywhere the number goes.")

    # ------------------------------------------------------------------ Q1
    print("\n" + "=" * 78)
    print("QUESTION 1 — the antecedent wetness Helene actually implies")
    print("=" * 78)
    w, status = solve_w(CAMPUS, H[CAMPUS], a.target)
    camp_in = tot[CAMPUS]
    print(f"  campus basin rainfall (measured) : {camp_in:.2f} in")
    print(f"  surveyed campus peak             : {a.target:,.0f} cfs")

    if status == "above":
        qmax = cwm_model.assess_event(CAMPUS, H[CAMPUS], 1.0)["calib_q"]
        print(f"\n  NO SOLUTION. Even saturated soil (w=1.0) yields only {qmax:,.0f} cfs.")
        print(f"  Shortfall {a.target - qmax:,.0f} cfs ({100*(a.target-qmax)/a.target:.0f}%).")
        print("  Meaning: the measured rainfall cannot produce the surveyed peak through")
        print("  this model at ANY antecedent condition. Either the radar is low — 20-33%")
        print("  is documented for this terrain — or the model under-produces. Both are")
        print("  findings, and they are distinguishable: see the sensitivity table.")
    elif status == "below":
        qmin = cwm_model.assess_event(CAMPUS, H[CAMPUS], 0.0)["calib_q"]
        print(f"\n  NO SOLUTION. Even bone-dry soil (w=0.0) yields {qmin:,.0f} cfs, which")
        print(f"  already EXCEEDS the surveyed {a.target:,.0f}. The model over-produces on")
        print("  measured rainfall — which would be a more serious finding than the ridge.")
    else:
        r = cwm_model.assess_event(CAMPUS, H[CAMPUS], w)
        print(f"\n  SOLVED:  w = {w:.3f}")
        print(f"  reproduces {r['calib_q']:,.0f} cfs, RP {r['rp_yr']} yr, "
              f"stage {r['stage_ft']:.2f} ft  (surveyed: {a.target:,.0f} / "
              f"{SURVEYED_RP_YR} / {SURVEYED_STAGE_FT})")
        print(f"  operative CN at that wetness: {r['CN']:.1f}")

        print("\n  Against the two records in conflict:")
        for name, desc, w0, w1 in (RECORD_WET, RECORD_DRY):
            verdict = "CONSISTENT" if w0 <= w <= w1 else "CONTRADICTED"
            print(f"    {verdict:<13} {desc:<26} (w {w0}-{w1})   [{name}]")
        if RECORD_WET[2] <= w <= RECORD_WET[3]:
            print("\n  -> Helene fell on WET ground. The 'drought-dry' reading is wrong,")
            print("     and with it the claim that the defining flood is an antecedent-")
            print("     moisture story. basins.py's ARC-III record is correct.")
        elif RECORD_DRY[2] <= w <= RECORD_DRY[3]:
            print("\n  -> Helene fell on DRY ground. The permeability note holds;")
            print("     basins.py's ARC-III record should be corrected.")
        else:
            print(f"\n  -> Helene fell on MIDDLING ground (w = {w:.2f}). BOTH records are")
            print("     wrong, and both should be corrected to the measured value.")

    # ------------------------------------------------------- Q1b: shape share
    print("\n" + "=" * 78)
    print("QUESTION 1b — how much of that came from the SHAPE, not the total?")
    print("=" * 78)
    wk, sk = solve_w(CAMPUS, k24a_shape(len(H[CAMPUS]), camp_in), a.target)
    print(f"  Same measured total ({camp_in:.2f} in), two hourly patterns:")
    print(f"    measured MRMS shape  -> w = "
          + (f"{w:.3f}" if status == "ok" else f"none ({status})"))
    print(f"    K24A valley shape    -> w = "
          + (f"{wk:.3f}" if sk == "ok" else f"none ({sk})"))
    if status == "ok" and sk == "ok":
        d = abs(w - wk)
        print(f"\n  Shape alone moves the answer by {d:.3f} in w"
              f" ({100*d/max(w, 1e-9):.0f}% of the solved value).")
        if d > 0.15:
            print("  That is larger than the gap between the two records this script is")
            print("  adjudicating. The disagreement in the docs was never really about")
            print("  soil — it was about which storm shape each author assumed.")
        else:
            print("  Small: the two shapes agree, so the total is doing the work here")
            print("  and the verdict above is robust to reasonable shape error.")
    else:
        print("\n  One of the two is off-ridge, which is itself the finding: at this")
        print("  total, whether the peak is reachable AT ALL depends on the shape.")

    # ------------------------------------------------------------------ Q2
    print("\n" + "=" * 78)
    print("QUESTION 2 — every basin under measured forcing")
    print("=" * 78)
    use_w = w if status == "ok" else 0.5
    if status != "ok":
        print(f"  (no solved w; using {use_w} so the comparison is still readable —")
        print("   these postures are illustrative, not a reconstruction)")
    print(f"{'basin':<16}{'rain in':>9}{'peak cfs':>11}{'RP yr':>8}{'posture':>12}"
          f"{'quality':>14}")
    print("-" * 78)
    for bid in cwm_model.ORDER:
        if bid not in H:
            print(f"{bid:<16}{'NOT OBSERVED — no posture can be stated':>62}")
            continue
        r = cwm_model.assess_event(bid, H[bid], use_w)
        g = gaps[bid]
        q = "measured" if g == 0 else f"{g} hrs MISSING"
        print(f"{bid:<16}{tot[bid]:>9.2f}{r['calib_q']:>11,.0f}{r['rp_yr']:>8}"
              f"{r['posture']:>12}{q:>14}")
    if any(gaps.values()):
        print("\n  Rows marked MISSING have zero-filled hours: their rainfall is a lower")
        print("  bound and their peak is biased low. Do not quote those postures bare.")

    # ------------------------------------------------------------------ Q3
    print("\n" + "=" * 78)
    print("QUESTION 3 — lockstep: physics, or an artifact of point forcing?")
    print("=" * 78)
    if len(tot) < 2:
        print("  Fewer than two basins observed. Nothing can be said about spread.")
    else:
        lo_in, hi_in = min(tot.values()), max(tot.values())
        mean_in = sum(tot.values()) / len(tot)
        rps = [cwm_model.assess_event(b, H[b], use_w)["rp_yr"] for b in cwm_model.ORDER
               if b in H]
        rps = [x for x in rps if x is not None]
        print(f"  basins observed               : {len(tot)} of {len(cwm_model.ORDER)}")
        print(f"  rainfall spread across basins : {hi_in-lo_in:.2f} in "
              f"({100*(hi_in-lo_in)/mean_in:.0f}% of mean)")
        if rps:
            print(f"  resulting return-period spread: {min(rps):.1f} .. {max(rps):.1f} yr")
        print("\n  For comparison, the documented behaviour under single-point forcing is a")
        print("  return-period spread of roughly 8.8-9.7 yr — under 10%, on basins ranging")
        print("  0.97 to 23.4 sq mi. That near-identity is what 'lockstep' means.")
        if (hi_in - lo_in) / mean_in < 0.05:
            print("\n  -> Measured rainfall was nearly uniform, so lockstep is largely REAL")
            print("     for this event. Caveat: a widespread tropical system is the case most")
            print("     likely to be uniform. This does not settle convective events, which")
            print("     are the ones that kill people in 29-minute basins.")
        else:
            print("\n  -> Measured rainfall was NOT uniform. The point-forcing path is")
            print("     discarding real spatial contrast, and basin-averaged QPE would")
            print("     separate the basins. This is direct evidence for the open fix.")

    print("\n" + "=" * 78)
    print("Whatever this says, write it into the project docs with the measured number")
    print("AND the gap fraction AND the shape sensitivity attached. The ridge existed")
    print("because two docs recorded two ends of it and neither recorded that it WAS")
    print("a ridge. Recording a bare number here would repeat exactly that mistake.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
