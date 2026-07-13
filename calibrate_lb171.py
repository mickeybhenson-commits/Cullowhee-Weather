"""
calibrate_lb171.py - in-basin event calibration of CC-LB-171 (Long Branch)
and nested/ad-hoc catchments against the WCHRS Hurricane Helene record.

STATUS OF THE WCHRS RECORD (per K. [WCHRS], Jul 2026):
  - Long Branch STAGE during Helene is NOT usable: the water-level sensor
    sedimented in. The pre-registered LB-171 stage closure cannot run as
    designed; the frozen prediction files remain frozen as a record of what
    was predicted.
  - Peak flow likely never exceeded bankfull at the Long Branch stilling
    well. That single constraint is a BINARY TEST of the frozen scenarios
    (see --bankfull-ft).
  - Gribble Gap (tributary nested inside CC-LB-171, ~0.17 mi2) has an
    EXCELLENT Helene discharge record. It is now the primary calibration
    target (see --adhoc-basin), with 5-min rain at Long Branch and 15-min
    rain at Upper/Lower Gribble Gap as forcing.
  - Cullowhee Creek main-stem rating + Helene HWM exist (separate harness
    concern; bears on CC-WCU-2260 thresholds).

WHY THIS BASIN FAMILY: co-observed rain + discharge inside the target basin
is the only thing that breaks the circular-calibration problem. This harness
holds the model side fixed and computes the correction the observed side
implies.

MODES
  A) MODEL-SIDE (runs today, no observed data):
       python calibrate_lb171.py
     Replays Helene forcing (~10 in / 3 days on drought antecedent, ARC I)
     through the operational chain (test_model TR-55/UH -> calibrate_peak ->
     rectangular rating -> posture) and prints the prediction, plus the
     ARC I/II/III sensitivity sweep.

     Bankfull constraint check (Katie's "never exceeded bankfull"):
       python calibrate_lb171.py --bankfull-ft 1.31
     (1.31 ft = CC-LB-171 bf_d_ft from cullowhee_roster.csv). Prints
     WITHIN-BANK / OVERBANK per ARC case. CAVEAT: the harness rates stage
     at the LB-171 pour-point rectangular section, not at the WCHRS
     stilling-well cross-section; treat a marginal result as inconclusive.

  B) CLOSE THE LOOP - roster basin (rated):
       python calibrate_lb171.py --obs-peak 820 --obs-runoff-in 3.1 --obs-ttp-hr 51.5
       python calibrate_lb171.py --hydrograph lb_helene_5min.csv --event-start "2024-09-25 08:00"
     Computes:
       k_event   observed peak / calibrated model peak -> event multiplier on
                 the (a,b) power-law calibration (a' = a * k_event, b unchanged)
       CN_event  back-solved from observed runoff depth + event rain total,
                 de-adjusted from ARC I back to an equivalent CN(II)
       Tc_factor solved so model time-to-peak matches observed
     and writes an OVERRIDE JSON in the cn_overrides.json pattern. Nothing
     operational is edited; you decide what gets incorporated.

  C) CLOSE THE LOOP - AD-HOC NESTED CATCHMENT (unrated), e.g. Gribble Gap:
       python calibrate_lb171.py --adhoc-basin gribble_gap.json \
           --hyeto-csv lb_rain_5min.csv \
           --hydrograph gg_helene_q.csv --event-start "2024-09-25 08:00"
     gribble_gap.json (values from WCHRS meeting / StreamStats delineation):
       {"name": "GRIBBLE-GAP", "DA_sqmi": 0.17, "CN2": 65, "Tc_min": 20,
        "note": "WCHRS flume; nested in CC-LB-171; params provisional"}
     In this mode there is no (a,b) calibration, no rating, no posture:
       k_event_raw = observed peak / RAW TR-55/UH peak - i.e. the event
       calibration of the uncalibrated model itself, the cleanest test the
       chain can get. CN back-solve and Tc factor work identically (on Q,
       runoff depth, and timing - no rating-curve uncertainty at all).

TRANSFER (printed with the results): CN(II) shift and Tc factor propagated
with provenance tags. Roster mode (B):
  in-basin              CC-LB-171
  similarity-transfer   CC-TIL-705, CC-SPD-1830, CC-MS-1100, CC-UP-503
  scale-mismatch        CC-COX-097  (0.97 mi2)
  weak                  CC-WCU-2260 (impervious + concrete channel; survey/FEMA
                                     carry this basin, not this transfer)
Ad-hoc Gribble Gap mode (C): the donor is the 0.17-mi2 flume catchment, so
  CC-COX-097 is promoted to scale-matched transfer, CC-LB-171 becomes
  nested-parent transfer, and the remaining tags carry a nested-donor caveat.
The peak multiplier (k_event / k_event_raw) is reported for the calibrated
catchment only; it is NOT auto-applied to other basins (one event, one
catchment, dry-end antecedent).

CAVEATS (carried into the JSON):
  - One event at one antecedent state (multi-year drought, GW at a 15-yr low).
    This pins the DRY end of the antecedent-response curve; wet-antecedent
    behavior remains model-extrapolated until the next co-observed storm.
  - Default daily rain split is an EQUAL-THIRDS APPROXIMATION of "~10 in over
    3 days". Replace with the WCHRS Long Branch 5-min gauge via --hyeto-csv
    (preferred, real temporal structure) or daily totals via --daily before
    trusting timing numbers.
  - --event-start anchors observed time-to-peak to rain onset instead of
    hydrograph-record start; without it, ttp comparisons are only valid if
    the discharge record begins at rain onset.

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

LB171_BANKFULL_FT = 1.31    # bf_d_ft, cullowhee_roster.csv (Wolman regional eq)

TRANSFER_TAGS_ROSTER = {
    "CC-LB-171":   "in-basin",
    "CC-TIL-705":  "similarity-transfer",
    "CC-SPD-1830": "similarity-transfer",
    "CC-MS-1100":  "similarity-transfer",
    "CC-UP-503":   "similarity-transfer",
    "CC-COX-097":  "scale-mismatch (donor should be Gribble Gap flume, 0.17 mi2)",
    "CC-WCU-2260": "weak (impervious/concrete channel; survey+FEMA govern)",
}

TRANSFER_TAGS_ADHOC = {
    "CC-LB-171":   "nested-parent transfer (donor nested inside this basin)",
    "CC-COX-097":  "scale-matched transfer (0.17 mi2 donor vs 0.97 mi2)",
    "CC-TIL-705":  "similarity-transfer (nested-donor caveat)",
    "CC-SPD-1830": "similarity-transfer (nested-donor caveat)",
    "CC-MS-1100":  "similarity-transfer (nested-donor caveat)",
    "CC-UP-503":   "similarity-transfer (nested-donor caveat)",
    "CC-WCU-2260": "weak (impervious/concrete channel; survey+FEMA govern)",
}


# ---------------------------------------------------------------------------
# basin context: roster basin (rated) or ad-hoc catchment (unrated)
# ---------------------------------------------------------------------------
def roster_basin(bid=BID):
    b = tm.BASINS[bid]
    return dict(bid=bid, name=bid, DA=b["DA"], CN2=b["CN2"], Tc=b["Tc"],
                rated=True)


def adhoc_basin(path):
    with open(path) as f:
        j = json.load(f)
    for k in ("DA_sqmi", "CN2", "Tc_min"):
        if k not in j:
            raise SystemExit(f"{path}: missing required key '{k}' "
                             "(need DA_sqmi, CN2, Tc_min; optional name, note)")
    return dict(bid=None, name=j.get("name", path), DA=float(j["DA_sqmi"]),
                CN2=float(j["CN2"]), Tc=float(j["Tc_min"]), rated=False,
                note=j.get("note", ""))


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
    time, second col inches. Returns (bins, start_time)."""
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
    return bins, t0


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


def model_case(hyeto, arc, basin, Tc_min=None, dt_hr=DT_HR):
    Tc = (Tc_min if Tc_min is not None else basin["Tc"]) / 60.0
    CN = tm.cn_adjust(basin["CN2"], arc)
    P = sum(hyeto)
    Q_in = tm.runoff_depth_in(P, CN)
    h = model_hydrograph(hyeto, CN, basin["DA"], Tc, dt_hr=dt_hr)
    qp = max(h)
    ttp_hr = h.index(qp) * dt_hr
    out = dict(CN=CN, P_in=P, Q_in=Q_in, qp=qp, ttp_hr=ttp_hr,
               calib_q=None, stage=None, posture=None)
    if basin["rated"]:
        cq = calibrate_peak(qp, basin["bid"])
        st = depth_from_q(cq, basin["bid"])
        out.update(calib_q=cq, stage=st, posture=posture(st, basin["bid"]))
    return out


# ---------------------------------------------------------------------------
# observed side
# ---------------------------------------------------------------------------
def read_observed_hydrograph(path, da_sqmi, event_start=None):
    """CSV (timestamp, q_cfs) at any regular step -> peak, runoff depth (in),
    time-to-peak. ttp is measured from event_start if given (rain onset -
    commensurate with model ttp), else from record start (only valid if the
    record begins at rain onset). Constant baseflow = pre-event minimum over
    the first 10% of the record; runoff = sum of (q - baseflow)+ * dt.
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
    da_sqft = da_sqmi * 5280.0 ** 2
    runoff_in = vol_cf / da_sqft * 12.0
    t_ref = event_start if event_start is not None else ts[0]
    if event_start is not None and event_start > ts[ip]:
        raise SystemExit("--event-start is after the observed peak - check timestamps")
    ttp_hr = (ts[ip] - t_ref).total_seconds() / 3600.0
    return dict(peak_cfs=qp, baseflow_cfs=q0, runoff_in=round(runoff_in, 2),
                ttp_hr=round(ttp_hr, 2), n=len(rows),
                ttp_ref=("event-start" if event_start is not None else "record-start"),
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


def solve_tc_factor(hyeto, arc, obs_ttp_hr, basin, lo=0.3, hi=3.0):
    """Tc multiplier such that model time-to-peak matches observed.
    Model ttp increases monotonically with Tc for a fixed hyetograph."""
    base_tc = basin["Tc"]
    def ttp(f):
        return model_case(hyeto, arc, basin, Tc_min=base_tc * f)["ttp_hr"]
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
def print_model_side(hyeto, arc, daily, basin, p5_in, bankfull_ft=None):
    print("=" * 78)
    print(f"MODEL-SIDE PREDICTION - {basin['name']}"
          f"{' (Long Branch)' if basin['bid'] == BID else ''}, Helene replay")
    print(f"  forcing: daily {['%.2f' % d for d in daily]} in "
          f"(total {sum(daily):.2f}), p5={p5_in} in -> ARC {arc}")
    print(f"  basin: DA={basin['DA']} mi2  Tc={basin['Tc']:.0f} min  "
          f"CN2={basin['CN2']:.0f}"
          f"{'' if basin['rated'] else '  [AD-HOC, unrated - discharge-only mode]'}")
    if not basin["rated"] and basin.get("note"):
        print(f"  note: {basin['note']}")
    print("=" * 78)
    r = model_case(hyeto, arc, basin)
    print(f"  effective CN (ARC {arc})      {r['CN']:8.1f}")
    print(f"  event rain                  {r['P_in']:8.2f} in")
    print(f"  runoff depth                {r['Q_in']:8.2f} in "
          f"(runoff ratio {r['Q_in']/r['P_in']:.2f})")
    print(f"  raw TR-55/UH peak           {r['qp']:8.0f} cfs")
    if basin["rated"]:
        print(f"  calibrated peak (a,b)       {r['calib_q']:8.0f} cfs   "
              f"[reg 100-yr = {REG[basin['bid']]['reg_q'][0.01]} cfs, TVA 100-yr = "
              f"{REG[basin['bid']]['tva_q'][100]} cfs]")
    print(f"  time-to-peak                {r['ttp_hr']:8.2f} hr from event start")
    if basin["rated"]:
        print(f"  rated stage (rectangular)   {r['stage']:8.2f} ft")
        print(f"  posture (PLACEHOLDER thr)   {r['posture']:>8}")
    print()
    print("  ARC sensitivity sweep (benchmark vs WCHRS HEC-HMS antecedent scenarios):")
    base = None
    for a in (1, 2, 3):
        s = model_case(hyeto, a, basin)
        if base is None:
            base = s
        pk = s["calib_q"] if basin["rated"] else s["qp"]
        pk0 = base["calib_q"] if basin["rated"] else base["qp"]
        line = (f"    ARC {a}: CN {s['CN']:5.1f}  peak {pk:7.0f} cfs "
                f"(x{pk/pk0:.2f})  ttp {s['ttp_hr']:5.2f} hr "
                f"({s['ttp_hr']-base['ttp_hr']:+.2f})")
        if basin["rated"]:
            line += f"  stage {s['stage']:.2f} ft  {s['posture']}"
            if bankfull_ft is not None:
                line += ("  [WITHIN-BANK]" if s["stage"] <= bankfull_ft
                         else "  [OVERBANK]")
        print(line)
    print("    -> their preliminary finding (wetter antecedent: higher peaks,")
    print("       shorter lags) should reproduce in sign here; the RATIO is the test.")
    if basin["rated"] and bankfull_ft is not None:
        print(f"\n  BANKFULL CONSTRAINT CHECK (observed: peak likely never exceeded")
        print(f"  bankfull at the WCHRS Long Branch stilling well; Helene):")
        print(f"    bankfull threshold          {bankfull_ft:6.2f} ft "
              f"(roster bf_d_ft = {LB171_BANKFULL_FT})")
        st = r["stage"]
        verdict = "WITHIN-BANK - consistent" if st <= bankfull_ft else \
                  "OVERBANK - model over-predicts vs the constraint"
        print(f"    ARC {arc} rated stage         {st:6.2f} ft  -> {verdict}")
        print(f"    CAVEAT: rated at the LB-171 pour-point rectangular section,")
        print(f"    not the stilling-well cross-section; marginal = inconclusive.")
    return r


def close_loop(mr, hyeto, arc, obs, args, basin):
    print("=" * 78)
    print(f"OBSERVED vs MODEL - {basin['name']}")
    print("=" * 78)
    out = {"basin": basin["name"], "event": "helene-2024", "arc": arc,
           "mode": "roster-rated" if basin["rated"] else "adhoc-unrated",
           "generated": datetime.datetime.now().isoformat(timespec="seconds"),
           "provenance": ("in-basin event calibration vs WCHRS record"
                          if basin["rated"] else
                          "nested-catchment event calibration vs WCHRS record "
                          "(ad-hoc basin, discharge-only)"),
           "caveat": ("single event, dry-end antecedent (multi-year drought); "
                      "wet-antecedent response remains extrapolated"),
           "basin_params": {"DA_sqmi": basin["DA"], "CN2": basin["CN2"],
                            "Tc_min": basin["Tc"]},
           "observed": obs, "model": {k: (round(v, 3) if isinstance(v, float) else v)
                                      for k, v in mr.items()}}

    # peak multiplier
    if basin["rated"]:
        k = obs["peak_cfs"] / mr["calib_q"]
        a, b = REG[basin["bid"]]["calib"]
        out["k_event"] = round(k, 3)
        out["calib_event"] = [round(a * k, 3), b]
        print(f"  observed peak               {obs['peak_cfs']:8.0f} cfs")
        print(f"  model calibrated peak       {mr['calib_q']:8.0f} cfs")
        print(f"  k_event                     {k:8.2f}   -> calib' = ({a * k:.3f}, {b})")
    else:
        k = obs["peak_cfs"] / mr["qp"]
        out["k_event_raw"] = round(k, 3)
        print(f"  observed peak               {obs['peak_cfs']:8.0f} cfs")
        print(f"  model RAW TR-55/UH peak     {mr['qp']:8.0f} cfs")
        print(f"  k_event_raw                 {k:8.2f}   (multiplier on the")
        print(f"    uncalibrated model - no (a,b) layer, no rating uncertainty)")

    # CN back-solve from runoff volume
    if obs.get("runoff_in"):
        cn_ev = backsolve_cn_event(mr["P_in"], obs["runoff_in"])
        if cn_ev:
            cn2 = cn1_to_cn2(cn_ev) if arc == 1 else cn_ev
            dcn = cn2 - basin["CN2"]
            out["cn_event_effective"] = round(cn_ev, 1)
            out["cn2_backsolved"] = round(cn2, 1)
            out["dCN2"] = round(dcn, 1)
            print(f"  observed runoff depth       {obs['runoff_in']:8.2f} in "
                  f"(model {mr['Q_in']:.2f})")
            print(f"  back-solved CN (event)      {cn_ev:8.1f}  -> CN2 equiv "
                  f"{cn2:.1f}  (dCN2 {dcn:+.1f} vs {basin['CN2']:.0f})")
        else:
            print("  ! runoff depth outside solvable range - check volume/baseflow")

    # Tc factor from time-to-peak
    if obs.get("ttp_hr"):
        if obs.get("ttp_ref") == "record-start":
            print("  NOTE: observed ttp referenced to RECORD start (no --event-start);")
            print("        only commensurate with model ttp if the record begins at rain onset.")
        f = solve_tc_factor(hyeto, arc, obs["ttp_hr"], basin)
        if f:
            out["tc_factor"] = round(f, 2)
            out["tc_event_min"] = round(basin["Tc"] * f)
            print(f"  observed time-to-peak       {obs['ttp_hr']:8.2f} hr "
                  f"(model {mr['ttp_hr']:.2f})")
            print(f"  Tc factor                   {f:8.2f}   -> Tc "
                  f"{basin['Tc']:.0f} -> {basin['Tc'] * f:.0f} min")
            print(f"    NOTE: apply as a RELATIONSHIP to other basins' own Tc;")
            print(f"    bears directly on the MS-1100 86-vs-142-min ambiguity.")
        else:
            print("  ! observed timing outside what Tc alone explains "
                  "(hyetograph structure? check --hyeto-csv vs default split)")

    # transfer table
    tags = TRANSFER_TAGS_ROSTER if basin["rated"] else TRANSFER_TAGS_ADHOC
    kname = "k_event" if basin["rated"] else "k_event_raw"
    print(f"\n  TRANSFER TABLE (CN2 shift + Tc factor; {kname} NOT transferred):")
    out["transfer"] = {}
    for tb, tag in tags.items():
        row = {"tag": tag}
        transferable = not tag.startswith(("scale-mismatch", "weak"))
        if "dCN2" in out and transferable:
            row["cn2_suggested"] = round(tm.BASINS[tb]["CN2"] + out["dCN2"], 1)
        if "tc_factor" in out and transferable:
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
    ap = argparse.ArgumentParser(description="LB-171 / nested-catchment Helene calibration harness")
    ap.add_argument("--adhoc-basin", default=None,
                    help="JSON for an unrated nested catchment (e.g. Gribble Gap): "
                         '{"name":..., "DA_sqmi":..., "CN2":..., "Tc_min":...}')
    ap.add_argument("--daily", nargs="+", type=float, default=None,
                    help="event daily rain totals, inches (replace the equal-thirds placeholder)")
    ap.add_argument("--hyeto-csv", default=None,
                    help="observed rain CSV (time, inches/interval) - overrides --daily")
    ap.add_argument("--p5", type=float, default=HELENE_P5_IN,
                    help="5-day antecedent rain, inches (default drought / ARC I)")
    ap.add_argument("--bankfull-ft", type=float, nargs="?", const=LB171_BANKFULL_FT,
                    default=None,
                    help="check rated stage vs bankfull depth, ft (bare flag uses "
                         f"roster LB-171 value {LB171_BANKFULL_FT}; rated basins only)")
    ap.add_argument("--obs-peak", type=float, default=None, help="observed peak, cfs")
    ap.add_argument("--obs-runoff-in", type=float, default=None,
                    help="observed event runoff depth, inches over the basin")
    ap.add_argument("--obs-ttp-hr", type=float, default=None,
                    help="observed time-to-peak, hours from event start")
    ap.add_argument("--hydrograph", default=None,
                    help="observed hydrograph CSV (time, q_cfs) - derives the three --obs-* values")
    ap.add_argument("--event-start", default=None,
                    help='rain-onset timestamp ("YYYY-MM-DD HH:MM") anchoring observed ttp')
    ap.add_argument("--out", default="lb171_calibration.json")
    args = ap.parse_args()

    basin = adhoc_basin(args.adhoc_basin) if args.adhoc_basin else roster_basin()
    if args.bankfull_ft is not None and not basin["rated"]:
        print("NOTE: --bankfull-ft ignored in ad-hoc mode (no rating available).\n")
        args.bankfull_ft = None

    daily = args.daily if args.daily else HELENE_DAILY_DEFAULT
    if args.daily is None and args.hyeto_csv is None:
        print("NOTE: using EQUAL-THIRDS placeholder for the Helene daily split -")
        print("      replace with the WCHRS 5-min gauge via --hyeto-csv or --daily.\n")
    if args.hyeto_csv:
        hyeto, rain_t0 = hyeto_from_csv(args.hyeto_csv)
        if args.event_start is None:
            args.event_start = rain_t0.isoformat()
            print(f"NOTE: --event-start not given; using rain-record start "
                  f"{args.event_start} as event onset.\n")
    else:
        hyeto = event_hyetograph(daily)
    arc = tm.arc_class(args.p5)

    mr = print_model_side(hyeto, arc,
                          daily if not args.hyeto_csv else [sum(hyeto)],
                          basin, args.p5, bankfull_ft=args.bankfull_ft)

    obs = None
    if args.hydrograph:
        ev0 = _parse_time(args.event_start) if args.event_start else None
        obs = read_observed_hydrograph(args.hydrograph, basin["DA"], event_start=ev0)
        print(f"\nOBSERVED (from {args.hydrograph}): peak {obs['peak_cfs']:.0f} cfs, "
              f"baseflow {obs['baseflow_cfs']:.1f}, runoff {obs['runoff_in']} in, "
              f"ttp {obs['ttp_hr']} hr [{obs['ttp_ref']}], {obs['n']} rows\n")
    elif args.obs_peak:
        obs = {"peak_cfs": args.obs_peak, "runoff_in": args.obs_runoff_in,
               "ttp_hr": args.obs_ttp_hr, "ttp_ref": "event-start"}

    if obs:
        close_loop(mr, hyeto, arc, obs, args, basin)
    else:
        print("\nNo observed data supplied - model side only. When the WCHRS numbers")
        print("arrive: --obs-peak/--obs-runoff-in/--obs-ttp-hr, or --hydrograph CSV")
        print("(+ --event-start), and --adhoc-basin gribble_gap.json for Gribble Gap.")


if __name__ == "__main__":
    main()
