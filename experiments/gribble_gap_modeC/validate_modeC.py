#!/usr/bin/env python3
"""
validate_modeC.py — can the pre-registered Gribble Gap calibration actually be fitted?

    python experiments/gribble_gap_modeC/validate_modeC.py

WHAT THIS ANSWERS
-----------------
`gribble_gap.json` is a frozen pre-registration: 38 events at the WCHRS flume
(GG-FLUME-018, 0.166 sq mi, nested inside CC-LB-171), 2015-08-13 to 2019-04-17,
with Hurricane Helene declared as the holdout before any WCHRS Helene data was
received. The plan is Mode C — fit `k_event_raw`, the ratio of observed peak to
raw TR-55/UH peak.

Nothing in the repo executed it. This does, and the answer is that **a single k
cannot be fitted from this dataset** — for two independent reasons, both
measurable here.

THE TWO REASONS
---------------
1. SHAPE. k is a clean function of event duration: median 0.09 for events of
   3 h or less, 1.07 for longer ones, spanning 1300x overall. The dataset carries
   event TOTALS, not hyetographs, so any peak comparison has to assume a shape.
   On a catchment whose Tc is ~10 minutes, the assumed shape dominates the answer.

2. INPUT DATA. The SCS-CN runoff DEPTH — which needs no hyetograph at all, so
   reason 1 cannot explain it — predicts ZERO runoff on 44% of events that
   measurably produced runoff, and over-predicts the rest by a median 3.5x. The
   split is entirely on the antecedent index, not on storm size. And the
   antecedent index comes from the same gauge as the storm rain: CUCN7, a valley
   point gauge 2.5 mi away at 2,193 ft against the catchment's 2,507 ft mean —
   the gauge documented in noah_cucn7_rain_reports_false_zeros_2026-08-08.md as
   emitting zeros through a measured storm.

   So the model's worst behaviour sits exactly where its input is least
   trustworthy, and this dataset cannot separate the two.

WHAT IT DOES NOT CLAIM
----------------------
That the model is wrong. It may be; the inputs may be; most likely both, in
proportions this data cannot resolve. What is established is that fitting k here
would be fitting to a confound.

The pre-registration already names the fix — "pending WCHRS 15-min gauges". This
quantifies why those gauges are not optional.

Stdlib only. Reads the three gribble_gap files from the repo root.
"""

import csv
import datetime as dt
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

import cwm_model as cwm                                     # noqa: E402
from wetness import resolve_wetness                         # noqa: E402

DT_HR = 0.0625          # 3.75 min — finer than both the 9.7-min Tc and the 10-min record


def load():
    gg = json.load(open(os.path.join(ROOT, "gribble_gap.json"), encoding="utf-8"))
    inv = {r["peak_time"]: r for r in csv.DictReader(
        open(os.path.join(ROOT, "gribble_gap_event_inventory_2015_2019.csv"), encoding="utf-8"))}
    mc = list(csv.DictReader(
        open(os.path.join(ROOT, "gribble_gap_modeC_input_table.csv"), encoding="utf-8")))
    return gg, inv, mc


def classify(mc):
    """Split the 38 rows into usable and excluded, with the reason recorded.

    Two exclusion classes, and they are NOT the same thing:
      * rain_obs_n == 0     -> no rain observations exist. An honest gap.
      * rain_obs_n > 0 and storm_rain_in == 0.0 -> observations exist and are ALL
        ZERO during measurable runoff. That is the CUCN7 false-zero signature,
        already documented for 2026 and visible here in the 2015-2019 archive.
    """
    usable, gaps, false_zero = [], [], []
    for r in mc:
        n, rain = int(r["rain_obs_n"]), float(r["storm_rain_in"])
        (gaps if n == 0 else false_zero if rain == 0.0 else usable).append(r)
    return usable, gaps, false_zero


def main():
    gg, inv, mc = load()
    DA = gg["streamstats"]["DA_adopted_sqmi"]
    CN2 = gg["model_parameters"]["CN2"]
    tc_hr = gg["model_parameters"]["Tc_kirpich_min"] / 60.0
    usable, gaps, false_zero = classify(mc)

    print("=" * 78)
    print("GRIBBLE GAP MODE C — can k be fitted?")
    print("=" * 78)
    print(f"  {gg['name']}  ({gg['basin_id']}), nests in {gg['nests_in']}")
    print(f"  DA {DA} sq mi · CN2 {CN2} · Tc {gg['model_parameters']['Tc_kirpich_min']} min (Kirpich)")
    print(f"  record {gg['gauge']['public_record']}")
    print(f"  holdout: {gg['calibration_plan']['holdout'][:60]}...")
    print()
    print(f"  events            : {len(mc)}")
    print(f"  usable            : {len(usable)}")
    print(f"  no rain data      : {len(gaps)}   {[r['peak_time'][:10] for r in gaps]}")
    print(f"  ALL-ZERO rain obs : {len(false_zero)}   {[r['peak_time'][:10] for r in false_zero]}")
    if false_zero:
        print("      ^ rain observations present, every one zero, during measurable runoff.")
        print("        Same signature as noah_cucn7_rain_reports_false_zeros_2026-08-08.md.")

    # ---- part 1: runoff DEPTH. No hyetograph, so shape cannot be blamed. -----
    print()
    print("=" * 78)
    print("1. RUNOFF DEPTH  —  shape-free: Q = (P-0.2S)^2/(P+0.8S) needs no hyetograph")
    print("=" * 78)
    zero, ratios, tot_m, tot_o, cns = [], [], 0.0, 0.0, []
    for r in usable:
        P, p5, obs = (float(r["storm_rain_in"]), float(r["antecedent_5d_in"]),
                      float(r["runoff_in"]))
        w, _ = resolve_wetness(p5_in=p5)
        CN = cwm.cn_from_wetness(CN2, w)
        S = 1000.0 / CN - 10.0
        Q = (P - 0.2 * S) ** 2 / (P + 0.8 * S) if P > 0.2 * S else 0.0
        cns.append(round(CN, 1))
        tot_m += Q
        tot_o += obs
        (zero if Q == 0 else ratios).append((r["peak_time"][:10], P, p5, CN, 0.2 * S, Q, obs))
    print(f"  model predicts ZERO runoff on {len(zero)} of {len(usable)} events "
          f"({100*len(zero)/len(usable):.0f}%) — every one measurably produced runoff")
    if zero:
        print(f"      their storm rain spans {min(z[1] for z in zero):.2f}–{max(z[1] for z in zero):.2f} in, "
              f"so it is not that the storms were small")
        print(f"      their 5-day antecedent: {sum(1 for z in zero if z[2] == 0.0)} rows read EXACTLY 0.00 in")
    if ratios:
        rr = sorted(q / o for _, _, _, _, _, q, o in ratios if o > 0)
        print(f"  where it does predict runoff: model/observed median {st.median(rr):.2f}, max {max(rr):.1f}")
    print(f"  TOTAL: model {tot_m:.2f} in vs observed {tot_o:.2f} in "
          f"= {100*tot_m/tot_o:.0f}% of measured runoff")
    top = sorted(set(cns), key=cns.count, reverse=True)[:2]
    print(f"  cn_from_wetness SATURATES: {cns.count(top[0])+cns.count(top[1])} of {len(cns)} events land on "
          f"just CN {top[0]} or CN {top[1]}")
    print("      -> initial abstraction swings 0.68 in to 3.64 in on the antecedent index alone,")
    print("         and that index comes from the same suspect gauge as the storm rain.")

    # ---- part 2: peak, and its dependence on the assumed shape ---------------
    print()
    print("=" * 78)
    print("2. PEAK  —  k = observed / raw model peak, uniform rain over the event duration")
    print("=" * 78)
    short, long_, all_k = [], [], []
    for r in usable:
        e = inv.get(r["peak_time"])
        if e is None or r["censored"] == "True":
            continue
        s = dt.datetime.fromisoformat(e["start"])
        en = dt.datetime.fromisoformat(e["end"])
        dur = max(DT_HR, (en - s).total_seconds() / 3600.0)
        w, _ = resolve_wetness(p5_in=float(r["antecedent_5d_in"]))
        CN = cwm.cn_from_wetness(CN2, w)
        n = max(1, int(round(dur / DT_HR)))
        qp = cwm.peak_discharge([float(r["storm_rain_in"]) / n] * n, CN, DA, tc_hr)
        if qp <= 0:
            continue
        k = float(e["peak_cfs"]) / qp
        all_k.append(k)
        (short if dur <= 3 else long_).append(k)
    if all_k:
        print(f"  n={len(all_k)}   k spans {min(all_k):.2f} to {max(all_k):.2f} "
              f"= {max(all_k)/min(all_k):.0f}x")
        if short:
            print(f"  events <= 3 h : n={len(short):>2}  median k {st.median(short):.2f}")
        if long_:
            print(f"  events >  3 h : n={len(long_):>2}  median k {st.median(long_):.2f}")
        print("  k is a function of DURATION, which the dataset does not constrain: it carries")
        print("  event totals, not hyetographs. On a Tc ~10 min catchment the assumed shape")
        print("  dominates the peak, so a single k_event_raw is not identifiable from totals.")

    print()
    print("=" * 78)
    print("CONCLUSION")
    print("=" * 78)
    print("  Mode C cannot be fitted from this dataset as it stands. Fitting k here would")
    print("  fit a confound: rainfall timing the data does not contain, and an antecedent")
    print("  index from a gauge that is known to emit zeros through measured storms.")
    print()
    print("  This does NOT show the model is wrong. It shows the experiment cannot")
    print("  currently distinguish a wrong model from wrong inputs.")
    print()
    print("  The pre-registration already names the fix — 'pending WCHRS 15-min gauges'.")
    print("  In-catchment rainfall at 15 minutes resolves both reasons at once: it gives")
    print("  the hyetograph the peak needs, and it replaces the antecedent index that is")
    print("  currently doing all the work.")
    print()
    print("  The Helene holdout stays clean either way — nothing here was fitted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
