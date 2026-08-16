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

VERIFIED AGAINST THE RAW RECORD, 2026-08-16 — AND ONE HALF DID NOT SURVIVE
---------------------------------------------------------------------------
Everything below was computed from the DERIVED inventory. On 2026-08-16 those derived
columns were finally checked against the flume record they came from
(wchrs_public/GribbleGap_Q_T_ALL_CLEAN_NOV19.csv, 221,697 rows, never previously read
by anything in this repo) using scripts/check_gribble_observed.py:

  peak_cfs   CONFIRMED, 38 of 38, exactly. The raw `disch` column is in LITRES PER
             SECOND — undocumented anywhere, recovered from the data — and
             round(raw_max / 28.3168, 2) equals peak_cfs for every event. Part 2 below
             therefore rests on numbers that reproduce.

  runoff_in  WAS NOT internally consistent — cause found, and correctable. Against the
             raw record, on well-sampled events:
                 before ~2016-08   ratio 2.020   (7 events)
                 after  ~2016-08   ratio 1.007   (10 events)
             After the break, runoff_in is total hydrograph volume over DA=0.166 sq mi
             across this table's own start->end window, to within 1% — which pins the
             method exactly, and shows runoff_in is NOT baseflow-separated. Before the
             break it is exactly twice that.

             THE CAUSE: the raw record samples every 5.0 min until 2016-04 and every
             10.0 min from 2016-05. A derivation assuming a FIXED 10-min step
             double-counted the 5-min half by exactly 10/5 = 2. The best-sampled early
             events read 2.021/2.002/2.004/2.004 — exactly 2 — and the step change falls
             in an event gap inside the measured break bracket. So the early rows are
             CORRECTABLE by an exact factor, not merely suspect, and Part 1 now reports
             a corrected pooled median as the quotable result.

Part 1 therefore shows both cohorts, notes that correcting the bug does NOT reconcile
them (they are small and simply different storm populations, so the cohort split is not
itself informative), and then reports the CORRECTED POOLED median over all events, which
is the number to quote.

Part 1's ZERO-runoff count is unaffected either way: it depends only on P, CN and Ia,
and never touches runoff_in.

ALSO NEW, AND IT QUALIFIES PART 2: the record's median sample interval is 10.0 minutes
and this catchment's Kirpich Tc is 9.7 minutes. It samples at its own response
timescale, so a true peak can rise and fall between samples. peak_cfs is a LOWER BOUND,
and the shortfall is worst on exactly the short intense events where k is smallest — so
k is biased low by an amount this data cannot quantify. That reinforces the conclusion
and means the k SPREAD below should not be quoted as a measurement.

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

# CAUSE FOUND 2026-08-16, and it is exact. The raw record samples every 5.0 min from
# 2015-08 to 2016-04 and every 10.0 min from 2016-05 on. A derivation that assumed a
# FIXED 10-min step therefore computed sum(Q * 10min) over data spaced 5 min apart, and
# double-counted the volume by exactly 10/5 = 2. Three facts agree: the best-sampled
# early events read 2.021/2.002/2.004/2.004 (exactly 2, not approximately); the record's
# step ratio is exactly 2; and the step change falls in an event gap inside the measured
# break bracket [2016-02-24 .. 2016-08-31]. An earlier draft put the break at 2016-08-25,
# which wrongly classified the 2016-08-19 event as early.
RUNOFF_BREAK = "2016-05-01"
# The measured size of the step, from scripts/check_gribble_observed.py: well-sampled
# events read 2.020x before the break and 1.007x after, against the raw record.
# 10 min assumed / 5 min actual. Exact by construction, not fitted to the residual —
# the 2.02 measured against the raw record is this number plus window truncation.
RUNOFF_FIX = 2.0


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
    cohort = {"early": [], "late": []}          # (model_in, observed_in) per event
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
        which = "early" if r["peak_time"][:10] < RUNOFF_BREAK else "late"
        cohort[which].append((Q, obs))
        (zero if Q == 0 else ratios).append((r["peak_time"][:10], P, p5, CN, 0.2 * S, Q, obs))
    print(f"  model predicts ZERO runoff on {len(zero)} of {len(usable)} events "
          f"({100*len(zero)/len(usable):.0f}%) — every one measurably produced runoff")
    if zero:
        print(f"      their storm rain spans {min(z[1] for z in zero):.2f}–{max(z[1] for z in zero):.2f} in, "
              f"so it is not that the storms were small")
        print(f"      their 5-day antecedent: {sum(1 for z in zero if z[2] == 0.0)} rows read EXACTLY 0.00 in")
    # NOT a pooled median. runoff_in relates to the raw record differently either side
    # of RUNOFF_BREAK (see the header), so one number over both cohorts averages two
    # different observed columns together and reports a quantity that does not exist.
    print()
    print(f"  where it does predict runoff, SPLIT at the {RUNOFF_BREAK} break in runoff_in:")
    print(f"      {'cohort':<22}{'n':>4}{'model/obs median':>19}{'model in':>11}{'obs in':>9}")
    meds = {}
    for name, label in (("early", f"before {RUNOFF_BREAK}"), ("late", f"after  {RUNOFF_BREAK}")):
        pairs = [(q, o) for q, o in cohort[name] if q > 0 and o > 0]
        if not pairs:
            print(f"      {label:<22}{0:>4}{'—':>19}")
            continue
        m = st.median(q / o for q, o in pairs)
        meds[name] = m
        print(f"      {label:<22}{len(pairs):>4}{m:>19.2f}"
              f"{sum(q for q, _ in pairs):>11.2f}{sum(o for _, o in pairs):>9.2f}")
    # An earlier draft of this block asserted that the cohort disagreement and the
    # measured 2.02x step in runoff_in were "the same fact". They are not, and the
    # numbers say so: removing the step does not reconcile the cohorts, so most of the
    # difference is storm composition, not scaling. Left here because the failed
    # reconciliation is more informative than the split was.
    if len(meds) == 2 and meds["late"]:
        raw = meds["early"] / meds["late"]
        corr = (meds["early"] * RUNOFF_FIX) / meds["late"]
        print(f"      -> cohorts disagree by {raw:.2f}x. Removing the {RUNOFF_FIX:.1f}x "
              f"fixed-step error leaves")
        print(f"         {corr:.2f}x — so the bug does NOT explain the gap, and these two")
        print(f"         cohorts are not comparable populations (n={len(cohort['early'])} vs "
              f"{len(cohort['late'])}, different storms).")
        print(f"      -> so the SPLIT is not the informative view. The correction below is.")
    # With the cause diagnosed and the factor exact, the early rows can be CORRECTED and
    # a pooled statistic becomes meaningful again. This is the one number to quote.
    fixed = [(q, o * (1.0 / RUNOFF_FIX)) for q, o in cohort["early"] if q > 0 and o > 0] \
          + [(q, o) for q, o in cohort["late"] if q > 0 and o > 0]
    if fixed:
        fm = st.median(q / o for q, o in fixed)
        fmod, fobs = sum(q for q, _ in fixed), sum(o for _, o in fixed)
        print()
        print(f"  CORRECTED — early runoff_in divided by the exact {RUNOFF_FIX:.1f}x "
              f"fixed-step error.")
        print(f"  This is the quotable view; the columns above are shown only to expose "
              f"the bug.")
        print(f"      events                : {len(fixed)} with non-zero modelled runoff")
        print(f"      model/observed median : {fm:.2f}")
        print(f"      totals                : model {fmod:.2f} in vs observed {fobs:.2f} in"
              f"  = {100*fmod/fobs:.0f}% of measured runoff")
        print(f"      (before the correction the totals read {tot_o:.2f} in observed, "
              f"{100*tot_m/tot_o:.0f}% — the")
        print(f"       median barely moves because the corrected events sit below it "
              f"either way, but the")
        print(f"       aggregate over-prediction rises from {100*tot_m/tot_o:.0f}% to "
              f"{100*fmod/fobs:.0f}%.)")
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
        print("  AND the observed side is a lower bound: the raw record samples every 10.0 min")
        print("  against a 9.7-min Tc, so a true peak can rise and fall between samples. The")
        print("  shortfall is worst on the short intense events where k is smallest, so k is")
        print("  biased LOW by an amount this data cannot quantify. Treat the span as an")
        print("  argument, not a measurement.")

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
    print("  A THIRD reason was added 2026-08-16, and it is neither model nor input: the")
    print("  observed runoff column does not have one consistent relationship to the record")
    print("  it was derived from. That is a PROVENANCE problem, and it only surfaced when")
    print("  somebody finally opened the raw file. See the header, and")
    print("  claude/noah_gribble_observed_verified_2026-08-16.md.")
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
