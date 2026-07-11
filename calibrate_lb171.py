"""
calibrate_lb171.py - in-basin event calibration of CC-LB-171 (Long Branch)
against the WCHRS Hurricane Helene record.

WHY LB-171: WCHRS's Long Branch rating-curve site (4.39 km2 = 1.70 mi2) is
effectively the CC-LB-171 pour point (1.71 mi2). Their Helene dataset (5-min
stage, flume + rating-curve discharge, rain, soil, ~40 wells) is the only
co-observed rain/soil/stage flood event in the watershed - the dataset that
breaks the circular-calibration problem. This harness holds the model side
fixed and computes the correction the observed side implies.

TWO MODES
  A) MODEL-SIDE (runs today, no observed data):
       python calibrate_lb171.py
     Replays Helene forcing (~10 in / 3 days on drought antecedent, ARC I)
     through the operational chain (test_model TR-55/UH -> calibrate_peak ->
     rectangular rating -> posture) and prints the prediction that the WCHRS
     record will be compared against, plus the ARC I/II/III sensitivity sweep
     (the benchmark against their HEC-HMS antecedent-scenario result).

  B) CLOSE THE LOOP (when WCHRS numbers arrive):
       python calibrate_lb171.py --obs-peak 820 --obs-runoff-in 3.1 --obs-ttp-hr 51.5
       python calibrate_lb171.py --hydrograph lb_helene_5min.csv
     Computes:
       k_event   observed peak / calibrated model peak  -> event multiplier on
                 the (a,b) power-law calibration (a' = a * k_event, b unchanged)
       CN_event  back-solved from observed runoff depth + event rain total,
                 de-adjusted from ARC I back to an equivalent CN(II)
       Tc_factor solved so the model time-to-peak matches the observed one
     and writes lb171_calibration.json - an OVERRIDE file in the
     cn_overrides.json pattern. Nothing operational is edited; you decide
     what gets incorporated.

TRANSFER (printed with the results): CN(II) shift and Tc factor propagated to
the similarity basins with provenance tags -
  in-basin              CC-LB-171
  similarity-transfer   CC-TIL-705, CC-SPD-1830, CC-MS-1100, CC-UP-503
  scale-mismatch        CC-COX-097   (0.97 mi2; wait for the Gribble Gap flume,
                                      0.17 mi2, as the scale-matched donor)
  weak                  CC-WCU-2260  (impervious + concrete channel; survey/FEMA
                                      carry this basin, not this transfer)
The peak multiplier k_event is reported for LB-171 only; it is NOT auto-applied
to other basins (one event, one basin, dry-end antecedent).

CAVEATS (carried into the JSON):
  - One event at one antecedent state (multi-year drought, GW at a 15-yr low).
    This pins the DRY end of the antecedent-response curve; wet-antecedent
    behavior remains model-extrapolated until the next co-observed storm.
  - Default daily rain split is an EQUAL-THIRDS APPROXIMATION of "~10 in over
    3 days". Replace with WCHRS gauge or MRMS daily totals via --daily before
    trusting the timing numbers.
  - Their Helene rain arrived over 3 days, not as a Type II 24-hr design storm;
    this harness concatenates scaled Type II days as the hyetograph. When the
    WCHRS 5-min rain record is in hand, feed it with --hyeto-csv for the real
    temporal structure.

Deps: standard library + test_model.py / flood_rating.py / basins.py alongside.
"""

import argparse
import csv
import datetime
import json
import math
import sys

import test_model as tm
from flood_rating import calibrate_peak, depth_from_q, posture
from basins import BASINS as REG

BID = "CC-LB-171"
DT_HR = 0.25

# Helene at Cullowhee: "about ten inches of rain fell over a three-day period"
# (WCHRS / GSA-SE 2026 abstract). EQUAL SPLIT IS A PLACEHOLDER - see --daily.
HELENE_TOTAL_IN = 10.0
HELENE_DAILY_DEFAULT = [HELENE_TOTAL_IN / 3.0] * 3
HELENE_P5_IN = 0.3          # drought antecedent -> ARC I (any p5 < 1.4 works)

TRANSFER_TAGS = {
    "CC-LB-171":   "in-basin",
    "CC-TIL-705":  "similarity-transfer",
    "CC-SPD-1830": "similarity-transfer",
    "CC-MS-1100":  "similarity-transfer",
    "CC-UP-503":   "similarity-transfer",
    "CC-COX-097":  "scale-mismatch (donor should be Gribble Gap flume, 0.17 mi2)",
    "CC-WCU-2260": "weak (impervious/concrete channel; survey+FEMA govern)",
}


# ---------------------------------------------------------------------------
# hyetograph: multi-day event as concatenated scaled Type II days
# ---------------------------------------------------------------------------
def event_hyetograph(daily_in, dt_hr=DT_HR):
    h = []
    for d in daily_in:
        h.extend(tm.storm_hyetograph(d, dt_hr=dt_hr))
    return h


def hyeto_from_csv(path, dt_hr=DT_HR):
    """CSV of (timestamp, rain_in per interval) at ANY regular step; resampled
    by simple accumulation onto the model dt. Column names free; first col
    time, second col inches."""
    rows = []
    with open(path) as f:
        for r in csv.reader(f):
            if len(r) < 2:
                continue
            try:
                t = _parse_time(r[0]); p = float(r[1])
            except (ValueError, TypeError):
                continue
            rows.append((t, p))
    if len(rows) < 2:
        raise SystemExit(f"no usable (time, rain) rows in {path}")
    rows.sort()
    t0 = rows[0][0]
    nbins = int(math.ceil((rows[-1][0] - t0).total_seconds() / 3600.0 / dt_hr)) + 1
    bins = [0.0] * nbins
    for t, p in rows:
        k = int((t - t0).total_seconds() / 3600.0 / dt_hr)
        bins[min(k, nbins - 1)] += p
    return bins


def _parse_time(s):
    s = s.strip()
    try:
        return datetime.datetime.fromtimestamp(float(s))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M", "%m/%d/%Y %H:%M"):
        try:
            return datetime.datetime.strptime(s[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    raise ValueError(s)


# ---------------------------------------------------------------------------
# model side: full hydrograph (not just the peak) so we get time-to-peak
# ---------------------------------------------------------------------------
def model_hydrograph(hyeto, CN, DA, Tc_hr, dt_hr=DT_HR):
    incr = tm.incremental_runoff(hyeto, CN)
    uh = tm.unit_hydrograph(DA, Tc_hr, dt_hr=dt_hr)
    h = [0.0] * (len(incr) + len(uh))
    for i, r in enumerate(incr):
        if r <= 0:
            continue
        for j, u in enumerate(uh):
            h[i + j] += r * u
    return h


def model_case(hyeto, arc, Tc_min=None, bid=BID, dt_hr=DT_HR):
    b = tm.BASINS[bid]
    Tc = (Tc_min if Tc_min is not None else b["Tc"]) / 60.0
    CN = tm.cn_adjust(b["CN2"], arc)
    P = sum(hyeto)
    Q_in = tm.runoff_depth_in(P, CN)
    h = model_hydrograph(hyeto, CN, b["DA"], Tc, dt_hr=dt_hr)
    qp = max(h)
    ttp_hr = h.index(qp) * dt_hr
    cq = calibrate_peak(qp, bid)
    stage = depth_from_q(cq, bid)
    return dict(CN=CN, P_in=P, Q_in=Q_in, qp=qp, calib_q=cq, ttp_hr=ttp_hr,
                stage=stage, posture=posture(stage, bid))


# ---------------------------------------------------------------------------
# observed side
# ---------------------------------------------------------------------------
def read_observed_hydrograph(path, bid=BID):
    """CSV (timestamp, q_cfs) at any regular step -> peak, runoff depth (in),
    time-to-peak (hr from record start). Constant baseflow = pre-event minimum
    over the first 10% of the record; runoff = sum of (q - baseflow)+ * dt.
    Crude by design - a harness, not a hydrograph-separation method."""
    rows = []
    with open(path) as f:
        for r in csv.reader(f):
            if len(r) < 2:
                continue
            try:
                t = _parse_time(r[0]); q = float(r[1])
            except (ValueError, TypeError):
                continue
            rows.append((t, q))
    if len(rows) < 10:
        raise SystemExit(f"no usable (time, q) rows in {path}")
    rows.sort()
    ts = [r[0] for r in rows]; qs = [r[1] for r in rows]
    q0 = min(qs[:max(3, len(qs) // 10)])
    qp = max(qs); ip = qs.index(qp)
    vol_cf = 0.0
    for i in range(1, len(rows)):
        dt_s = (ts[i] - ts[i - 1]).total_seconds()
        vol_cf += max(0.0, 0.5 * (qs[i] + qs[i - 1]) - q0) * dt_s
    da_sqft = REG[BID]["da_sqmi"] * 5280.0 ** 2
    runoff_in = vol_cf / da_sqft * 12.0
    ttp_hr = (ts[ip] - ts[0]).total_seconds() / 3600.0
    return dict(peak_cfs=qp, baseflow_cfs=q0, runoff_in=round(runoff_in, 2),
                ttp_hr=round(ttp_hr, 2), n=len(rows),
                start=ts[0].isoformat(), end=ts[-1].isoformat())


# ---------------------------------------------------------------------------
# solvers
# ---------------------------------------------------------------------------
def backsolve_cn_event(P_in, Q_obs_in, lo=30.0, hi=98.0):
    """CN such that SCS runoff(P, CN) = observed runoff depth."""
    if Q_obs_in <= 0 or Q_obs_in >= P_in:
        return None
    for _ in range(80):
        m = 0.5 * (lo + hi)
        if tm.runoff_depth_in(P_in, m) < Q_obs_in:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)


def cn1_to_cn2(cn1, lo=30.0, hi=98.0):
    """Invert the NRCS dry relation: find CN2 with cn_adjust(CN2, ARC I) = cn1."""
    for _ in range(80):
        m = 0.5 * (lo + hi)
        if tm.cn_adjust(m, 1) < cn1:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)


def solve_tc_factor(hyeto, arc, obs_ttp_hr, bid=BID, lo=0.3, hi=3.0):
    """Tc multiplier such that model time-to-peak matches observed.
    Model ttp increases monotonically with Tc for a fixed hyetograph."""
    base_tc = tm.BASINS[bid]["Tc"]
    def ttp(f):
        return model_case(hyeto, arc, Tc_min=base_tc * f, bid=bid)["ttp_hr"]
    if not (ttp(lo) <= obs_ttp_hr <= ttp(hi)):
        return None   # observed timing outside what Tc alone can explain
    for _ in range(40):
        m = 0.5 * (lo + hi)
        if ttp(m) < obs_ttp_hr:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def print_model_side(hyeto, arc, daily):
    b = tm.BASINS[BID]
    print("=" * 78)
    print(f"MODEL-SIDE PREDICTION - {BID} (Long Branch), Helene replay")
    print(f"  forcing: daily {['%.2f' % d for d in daily]} in "
          f"(total {sum(daily):.2f}), p5={HELENE_P5_IN} in -> ARC {arc}")
    print(f"  basin: DA={b['DA']} mi2  Tc={b['Tc']} min  CN2={b['CN2']}")
    print("=" * 78)
    r = model_case(hyeto, arc)
    print(f"  effective CN (ARC {arc})      {r['CN']:8.1f}")
    print(f"  event rain                  {r['P_in']:8.2f} in")
    print(f"  runoff depth                {r['Q_in']:8.2f} in "
          f"(runoff ratio {r['Q_in']/r['P_in']:.2f})")
    print(f"  raw TR-55/UH peak           {r['qp']:8.0f} cfs")
    print(f"  calibrated peak (a,b)       {r['calib_q']:8.0f} cfs   "
          f"[reg 100-yr = {REG[BID]['reg_q'][0.01]} cfs, TVA 100-yr = "
          f"{REG[BID]['tva_q'][100]} cfs]")
    print(f"  time-to-peak                {r['ttp_hr']:8.2f} hr from event start")
    print(f"  rated stage (rectangular)   {r['stage']:8.2f} ft")
    print(f"  posture (PLACEHOLDER thr)   {r['posture']:>8}")
    print()
    print("  ARC sensitivity sweep (benchmark vs WCHRS HEC-HMS antecedent scenarios):")
    base = None
    for a in (1, 2, 3):
        s = model_case(hyeto, a)
        if base is None:
            base = s
        print(f"    ARC {a}: CN {s['CN']:5.1f}  peak {s['calib_q']:7.0f} cfs "
              f"(x{s['calib_q']/base['calib_q']:.2f})  ttp {s['ttp_hr']:5.2f} hr "
              f"({s['ttp_hr']-base['ttp_hr']:+.2f})  stage {s['stage']:.2f} ft  {s['posture']}")
    print("    -> their preliminary finding (wetter antecedent: higher peaks,")
    print("       shorter lags) should reproduce in sign here; the RATIO is the test.")
    return r


def close_loop(mr, hyeto, arc, obs, args):
    print("=" * 78)
    print(f"OBSERVED vs MODEL - {BID}")
    print("=" * 78)
    out = {"basin": BID, "event": "helene-2024", "arc": arc,
           "generated": datetime.datetime.now().isoformat(timespec="seconds"),
           "provenance": "in-basin event calibration vs WCHRS Long Branch record",
           "caveat": ("single event, dry-end antecedent (multi-year drought); "
                      "wet-antecedent response remains extrapolated"),
           "observed": obs, "model": {k: round(v, 3) if isinstance(v, float) else v
                                      for k, v in mr.items()}}

    # peak multiplier -> event-adjusted (a, b)
    k = obs["peak_cfs"] / mr["calib_q"]
    a, b = REG[BID]["calib"]
    out["k_event"] = round(k, 3)
    out["calib_event"] = [round(a * k, 3), b]
    print(f"  observed peak               {obs['peak_cfs']:8.0f} cfs")
    print(f"  model calibrated peak       {mr['calib_q']:8.0f} cfs")
    print(f"  k_event                     {k:8.2f}   -> calib' = ({a * k:.3f}, {b})")

    # CN back-solve from runoff volume
    if obs.get("runoff_in"):
        cn_ev = backsolve_cn_event(mr["P_in"], obs["runoff_in"])
        if cn_ev:
            cn2 = cn1_to_cn2(cn_ev) if arc == 1 else cn_ev
            dcn = cn2 - tm.BASINS[BID]["CN2"]
            out["cn_event_effective"] = round(cn_ev, 1)
            out["cn2_backsolved"] = round(cn2, 1)
            out["dCN2"] = round(dcn, 1)
            print(f"  observed runoff depth       {obs['runoff_in']:8.2f} in "
                  f"(model {mr['Q_in']:.2f})")
            print(f"  back-solved CN (event)      {cn_ev:8.1f}  -> CN2 equiv "
                  f"{cn2:.1f}  (dCN2 {dcn:+.1f} vs {tm.BASINS[BID]['CN2']})")
        else:
            print("  ! runoff depth outside solvable range - check volume/baseflow")

    # Tc factor from time-to-peak
    if obs.get("ttp_hr"):
        f = solve_tc_factor(hyeto, arc, obs["ttp_hr"])
        if f:
            out["tc_factor"] = round(f, 2)
            out["tc_event_min"] = round(tm.BASINS[BID]["Tc"] * f)
            print(f"  observed time-to-peak       {obs['ttp_hr']:8.2f} hr "
                  f"(model {mr['ttp_hr']:.2f})")
            print(f"  Tc factor                   {f:8.2f}   -> Tc "
                  f"{tm.BASINS[BID]['Tc']} -> {tm.BASINS[BID]['Tc'] * f:.0f} min")
            print(f"    NOTE: apply as a RELATIONSHIP to other basins' own Tc;")
            print(f"    bears directly on the MS-1100 86-vs-142-min ambiguity.")
        else:
            print("  ! observed timing outside what Tc alone explains "
                  "(hyetograph structure? check --hyeto-csv vs default split)")

    # transfer table
    print("\n  TRANSFER TABLE (CN2 shift + Tc factor; k_event NOT transferred):")
    out["transfer"] = {}
    for tb, tag in TRANSFER_TAGS.items():
        row = {"tag": tag}
        if "dCN2" in out and tag.startswith(("in-basin", "similarity")):
            row["cn2_suggested"] = round(tm.BASINS[tb]["CN2"] + out["dCN2"], 1)
        if "tc_factor" in out and tag.startswith(("in-basin", "similarity")):
            row["tc_suggested_min"] = round(tm.BASINS[tb]["Tc"] * out["tc_factor"])
        out["transfer"][tb] = row
        extra = ", ".join(f"{k2}={v2}" for k2, v2 in row.items() if k2 != "tag")
        print(f"    {tb:14s} {tag:<58s} {extra}")

    path = args.out
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n  wrote {path}  (override file - nothing operational edited)")
    return out


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="CC-LB-171 Helene calibration harness")
    ap.add_argument("--daily", nargs="+", type=float, default=None,
                    help="event daily rain totals, inches (replace the equal-thirds placeholder)")
    ap.add_argument("--hyeto-csv", default=None,
                    help="observed rain CSV (time, inches/interval) - overrides --daily")
    ap.add_argument("--p5", type=float, default=HELENE_P5_IN,
                    help="5-day antecedent rain, inches (default drought / ARC I)")
    ap.add_argument("--obs-peak", type=float, default=None, help="observed peak, cfs")
    ap.add_argument("--obs-runoff-in", type=float, default=None,
                    help="observed event runoff depth, inches over the basin")
    ap.add_argument("--obs-ttp-hr", type=float, default=None,
                    help="observed time-to-peak, hours from event start")
    ap.add_argument("--hydrograph", default=None,
                    help="observed hydrograph CSV (time, q_cfs) - derives the three --obs-* values")
    ap.add_argument("--out", default="lb171_calibration.json")
    args = ap.parse_args()

    daily = args.daily if args.daily else HELENE_DAILY_DEFAULT
    if args.daily is None and args.hyeto_csv is None:
        print("NOTE: using EQUAL-THIRDS placeholder for the Helene daily split -")
        print("      replace with WCHRS gauge / MRMS daily totals via --daily.\n")
    hyeto = hyeto_from_csv(args.hyeto_csv) if args.hyeto_csv else event_hyetograph(daily)
    arc = tm.arc_class(args.p5)

    mr = print_model_side(hyeto, arc, daily if not args.hyeto_csv else [sum(hyeto)])

    obs = None
    if args.hydrograph:
        obs = read_observed_hydrograph(args.hydrograph)
        print(f"\nOBSERVED (from {args.hydrograph}): peak {obs['peak_cfs']:.0f} cfs, "
              f"baseflow {obs['baseflow_cfs']:.1f}, runoff {obs['runoff_in']} in, "
              f"ttp {obs['ttp_hr']} hr, {obs['n']} rows\n")
    elif args.obs_peak:
        obs = {"peak_cfs": args.obs_peak, "runoff_in": args.obs_runoff_in,
               "ttp_hr": args.obs_ttp_hr}

    if obs:
        close_loop(mr, hyeto, arc, obs, args)
    else:
        print("\nNo observed data supplied - model side only. When the WCHRS numbers")
        print("arrive: --obs-peak/--obs-runoff-in/--obs-ttp-hr, or --hydrograph CSV.")


if __name__ == "__main__":
    main()
