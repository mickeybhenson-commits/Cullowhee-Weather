"""
fit_surveyed_rating.py - graduate an on-profile reach to a SURVEYED stage-discharge
rating from a HEC-RAS multi-frequency WSE export, so its FIS/BFE thresholds become
valid (the campus-TVA pattern, generalized).

WHY THIS EXISTS
  flood_rating rates the campus on a surveyed power law Q = C*depth^B fit to TVA's
  WSE-by-frequency; the tributaries fall back to an in-bank Bieger rectangle. The
  rectangle CANNOT reach a floodplain-stage threshold at realistic discharge (a
  100-yr-BFE EMERGENCY on Speedwell needs ~12,700 cfs through a 55.7-ft rectangle
  vs a 500-yr of 5,660). So any reach whose EMERGENCY rung is a floodplain stage
  (every on-profile mainstem reach) needs a surveyed rating, not a rectangle.

  The fit itself mirrors flood_rating._tva_rating. This module is the INGESTION +
  QA layer around it: reduce the export to (depth-above-invert, Q), fit, check the
  fit quality and the discharge basis (FIS Q vs StreamStats), and emit the exact
  basins.py fields to paste in.

INPUT (from HEC-RAS, at the reach's cross-section / pour RM)
  wse_by_freq : {return_period_yr: (discharge_cfs, wse_ft_navd88)}  >= 2 entries
                (e.g. the 10/50/100/500-yr profiles at RM 13211 for Speedwell)
  invert_el_ft: thalweg/invert elevation at that XS (NAVD88). depth = WSE - invert,
                which is flood_rating's rating datum (datum-free, matches the BFE CSV).

OUTPUT
  fit_surveyed_rating(...) -> dict with C, B, R^2, the depth points, a QA verdict,
  and `record_fields` ready to drop into the basin's dict in basins.py:
      rating   = "surveyed"      # treat like "tva" in the engine (see WIRING)
      wse      = {rp: (Q, WSE)}  # the export, stored for reproducibility
      bed_ft   = invert_el_ft
  After pasting, set the reach's thr_ft to its surveyed BFE ladder.

WIRING (one-time, in flood_rating.py - "surveyed" reuses the generic power-law path)
  depth_from_q:  if rec["rating"] in ("tva", "surveyed"):  C,B = _tva_rating(rec) ...
  q_from_depth:  same one-line change.
  _tva_rating already reads rec["tva_wse"]; either store under "tva_wse" OR add
  `rec.get("wse") or rec.get("tva_wse")` there. (Two characters; kept out of this
  file so the engine stays the single source of the rating math.)
"""

import math


def _fit_power_law(depths, discharges):
    """Q = C * depth^B by least squares in log-log space. Returns (C, B, R2)."""
    lx = [math.log(d) for d in depths]
    ly = [math.log(q) for q in discharges]
    n = len(lx)
    mx, my = sum(lx) / n, sum(ly) / n
    sxx = sum((x - mx) ** 2 for x in lx)
    B = sum((x - mx) * (y - my) for x, y in zip(lx, ly)) / sxx
    lnC = my - B * mx
    C = math.exp(lnC)
    # R^2 in log space
    ss_res = sum((y - (lnC + B * x)) ** 2 for x, y in zip(lx, ly))
    ss_tot = sum((y - my) ** 2 for y in ly)
    R2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return C, B, R2


def fit_surveyed_rating(wse_by_freq, invert_el_ft, reg_q=None, basin_id=None):
    """Fit a surveyed Q=C*depth^B rating and QA it. reg_q (optional) = the basin's
    StreamStats reg_q {AEP: Q} to check the FIS discharge basis (campus agreed ~10%)."""
    AEP_FOR_RP = {2: 0.50, 5: 0.20, 10: 0.10, 25: 0.04, 50: 0.02,
                  100: 0.01, 200: 0.005, 500: 0.002}
    if len(wse_by_freq) < 2:
        raise ValueError("need >= 2 frequency WSE points to fit a rating")

    rows = []
    for rp, (q, wse) in sorted(wse_by_freq.items()):
        depth = wse - invert_el_ft
        rows.append((rp, q, wse, depth))
    depths = [r[3] for r in rows]
    discharges = [r[1] for r in rows]

    # QA 1: physical depths, monotonic stage with discharge
    notes = []
    if any(d <= 0 for d in depths):
        notes.append("** WSE at/below invert - check invert datum **")
    if any(depths[i + 1] <= depths[i] for i in range(len(depths) - 1)):
        notes.append("** non-monotonic stage vs frequency - check export **")

    C, B, R2 = _fit_power_law(depths, discharges)
    if R2 < 0.99:
        notes.append(f"** fit R2={R2:.4f} < 0.99 - rating may be poorly described by a single power law **")

    # QA 2: discharge basis - FIS Q vs StreamStats reg_q at matching frequency
    basis = []
    if reg_q:
        for rp, q, _, _ in rows:
            aep = AEP_FOR_RP.get(rp)
            rq = reg_q.get(aep) if aep else None
            if rq:
                basis.append((rp, q, rq, 100.0 * (q - rq) / rq))
        worst = max((abs(d) for *_, d in basis), default=0.0)
        if worst > 25:
            notes.append(f"** FIS vs StreamStats discharge differs up to {worst:.0f}% - "
                         f"feeding StreamStats-calibrated Q into a FIS rating may bias stage **")

    def depth_from_q(q):       # the resulting rating (inverse of Q=C*depth^B)
        return (q / C) ** (1.0 / B) if q and q > 0 else 0.0

    return {
        "basin_id": basin_id,
        "C": C, "B": B, "R2": R2,
        "points": rows,                       # (rp, Q, WSE, depth)
        "discharge_basis": basis,             # (rp, FIS_Q, reg_Q, dev%)
        "qa_notes": notes or ["clean: monotonic, R2>=0.99, discharge basis OK"],
        "depth_from_q": depth_from_q,
        "record_fields": {
            "rating": "surveyed",
            "wse": {rp: (q, wse) for rp, q, wse, _ in rows},
            "bed_ft": invert_el_ft,
        },
    }


def report(fit):
    f = fit
    print(f"Surveyed rating{(' - ' + f['basin_id']) if f['basin_id'] else ''}: "
          f"Q = {f['C']:.4f} * depth^{f['B']:.4f}   (R2={f['R2']:.4f})")
    print(f"  {'RP':>5}{'Q_cfs':>9}{'WSE':>10}{'depth':>8}")
    for rp, q, wse, depth in f["points"]:
        print(f"  {rp:>4}y{q:>9,.0f}{wse:>10.1f}{depth:>8.2f}")
    if f["discharge_basis"]:
        print("  discharge basis (FIS vs StreamStats):")
        for rp, fq, rq, dev in f["discharge_basis"]:
            print(f"    {rp:>4}y  FIS {fq:>6,.0f}  StreamStats {rq:>6,.0f}  ({dev:+.0f}%)")
    print("  QA: " + " ; ".join(f["qa_notes"]))
    print("  basins.py record_fields ->")
    rf = f["record_fields"]
    print(f"    rating=\"surveyed\",")
    print(f"    wse={{" + ", ".join(f"{rp}:({q:.0f},{wse:.1f})" for rp,(q,wse) in rf['wse'].items()) + "},")
    print(f"    bed_ft={rf['bed_ft']},")


# ---------------------------------------------------------------------------
# SELF-TEST on SYNTHETIC Speedwell WSE (placeholders! replace with HEC-RAS export).
# Anchored to real points we DO have: invert 2115.1 ft and 100-yr BFE 2124.3 ft
# (depth 9.2) at RM 13211, plus StreamStats discharges. The 10/50/500-yr stages
# here are fabricated to lie on a plausible rating - they are NOT surveyed values.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    INVERT = 2115.1                      # RM 13211 invert (real, from BFE CSV)
    SPD_REG_Q = {0.50:829, 0.20:1470, 0.10:2010, 0.04:2740, 0.02:3410,
                 0.01:4050, 0.005:4740, 0.002:5660}   # StreamStats, basins.py
    # SYNTHETIC (rp -> (discharge, WSE)); 100-yr WSE is real (2124.3), others invented
    SYNTH = {10:  (2010, 2121.7),        # depth ~6.6
             50:  (3410, 2123.5),        # depth ~8.4
             100: (4050, 2124.3),        # depth  9.2  (REAL BFE)
             500: (5660, 2125.8)}        # depth ~10.7
    print("SELF-TEST - SYNTHETIC Speedwell WSE (illustrative; not surveyed):\n")
    fit = fit_surveyed_rating(SYNTH, INVERT, reg_q=SPD_REG_Q, basin_id="CC-SPD-1830")
    report(fit)

    # Close the loop: what would the calibrated 100-yr (4057 cfs) post on this rating?
    d100 = fit["depth_from_q"](4057)
    THR = (2.7, 6.5, 9.2)               # bankfull / top-of-bank / BFE (depth above invert)
    post = ("EMERGENCY" if d100 >= THR[2] else "WARNING" if d100 >= THR[1]
            else "WATCH" if d100 >= THR[0] else "NORMAL")
    print(f"\n  calibrated 100-yr 4,057 cfs -> {d100:.1f} ft depth -> posture {post}")
    print("  (with a surveyed rating the BFE thresholds 2.7/6.5/9.2 are now REACHABLE -")
    print("   the rectangle could not get there; this is the fix for the Speedwell gap.)")
    print("\n  >>> Replace SYNTH with real HEC-RAS WSE at RM 13211 (>=2 of 10/50/100/500-yr),")
    print("      paste record_fields into basins.py, set thr_ft to the BFE ladder. Done.")
