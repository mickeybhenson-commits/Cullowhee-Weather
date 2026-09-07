#!/usr/bin/env python3
"""
reach_rating.py — LiDAR-section ratings for the five reaches that still ran on regression rectangles:
Mountain (CC-UP-503), Lower Mountain (CC-MS-1100), Tilley Creek (CC-TIL-705), Cox Branch (CC-COX-097),
Long Branch (CC-LB-171).

WHY. The stage column on live.html is a countdown to WATCH. On these reaches the WATCH rung came from
surveyed LiDAR sections (thr_ft in basins.py) but the LEVEL came from a regional-regression rectangle at
a steep slope — the same idealisation known to be ~2x too shallow at Speedwell — so the level and the rung
were in different frames and the margins were too generous. This puts both halves on one section per
reach, cut from USGS 3DEP 1 m (NC QL2 LiDAR) at the pour point (data/reach_sections_3dep.json), the same
method as fiman_rating / campus_rating / mouth_rating: conveyance Manning through the real geometry,
parabolic unseen bed 1 ft below the LiDAR water surface, water-surface slope from a 4 m thalweg grid,
channel n per reach (0.045 gravel/cobble; 0.05 Cox; 0.06 the boulder ravine at Mountain), overbank 0.08.

WATCH rung = the stage this same rating gives at the 2-yr regression flow. That is the one choice that keeps
the countdown consistent with the posture rule (these reaches go to WATCH at >= 2-yr flow), and bankfull
~ 2-yr is the textbook relation anyway. The section's own lower bank and the registry's surveyed bankfull
(+1 ft of unseen bed) are stored alongside as checks; Mountain's registry value is still a placeholder. Depth above the
inferred thalweg. +-35 %. All of this is replaced by the stage nodes when they report; it exists so the
eight rows are on the same footing until then.

    python reach_rating.py     -> writes data/reach_ratings.json, prints a check table
"""
from __future__ import annotations

import json
from pathlib import Path

import fiman_rating as fr

HERE = Path(__file__).resolve().parent
SECTION_JSON = HERE / "data" / "reach_sections_3dep.json"
RATING_JSON = HERE / "data" / "reach_ratings.json"
BED_BELOW_WS = 1.0
QB = {"CC-UP-503": 10.1, "CC-MS-1100": 22.0, "CC-TIL-705": 14.1, "CC-COX-097": 1.9, "CC-LB-171": 3.4}   # cwm_model baseflows
THR_REGISTRY = {"CC-UP-503": 1.78, "CC-MS-1100": 1.65, "CC-TIL-705": 1.77, "CC-COX-097": 1.67, "CC-LB-171": 1.65}
Q2 = {"CC-UP-503": 269, "CC-MS-1100": 532, "CC-TIL-705": 361, "CC-COX-097": 64.3, "CC-LB-171": 105}   # StreamStats 2-yr (= cwm_model.BASINS[bid]["reg_q"][0.50])


def _section(rec):
    elev = list(rec["elev_ft"])
    sta = [float(i) * 3.28084 for i in range(len(elev))]
    mn = min(elev)
    th = rec.get("thresh", 0.35)
    m = elev.index(mn)                       # the wetted run is the CONTIGUOUS low stretch around the minimum
    lo = m
    while lo > 0 and elev[lo - 1] <= mn + th:
        lo -= 1
    hi = m
    while hi < len(elev) - 1 and elev[hi + 1] <= mn + th:
        hi += 1
    c = (lo + hi) / 2.0
    half = (hi - lo) / 2.0 or 1.0
    bed = list(elev)
    for i in range(lo, hi + 1):
        bed[i] = mn - BED_BELOW_WS * (1.0 - ((i - c) / half) ** 2)
    return sta, bed, elev, lo, hi, mn


def _bank_ft(elev, lo, hi, thal):
    """Lower bank above the thalweg: walking outward from the water on each side, the first point where the
    ground stops rising (rise < 0.15 ft over the next 3 m) after climbing at least 1.2 ft above the water."""
    ws = min(elev)
    banks = []
    for step, start, stop in ((-1, lo, 0), (1, hi, len(elev) - 1)):
        i = start
        while (i + 3 * step) * step <= stop * step:
            z = elev[i]
            if z - ws >= 1.2 and elev[i + 3 * step] - z < 0.15:
                banks.append(z - thal)
                break
            i += step
        else:
            banks.append(elev[stop] - thal)
    b = min(banks)
    return round(max(1.2, min(6.0, b)), 2)


def build(step=0.1, dmax=14.0):
    d = json.loads(SECTION_JSON.read_text(encoding="utf-8"))
    out = dict(source=d["source"], built="2026-09-07", method="conveyance-weighted Manning through a 3DEP LiDAR section at the pour point; "
               "depth above the inferred thalweg (parabolic bed 1 ft below the LiDAR water surface); WATCH = stage at the 2-yr regression flow through the same rating",
               uncertainty=fr.UNCERTAINTY, reaches={})
    for bid, rec in d["reaches"].items():
        sta, bed, elev, lo, hi, ws = _section(rec)
        thal = min(bed)
        ch = (sta[lo] - 10.0, sta[hi] + 10.0)
        table = []
        x = 0.0
        while x <= dmax + 1e-9:
            table.append([round(x, 1), round(fr.conveyance_q(sta, bed, thal + x, slope=rec["slope"], ch_range=ch,
                                                             n_ch=rec.get("n_channel", fr.N_CHANNEL)), 1)])
            x += step
        out["reaches"][bid] = dict(name=rec["name"], thalweg_ft=round(thal, 2), lidar_ws_ft=ws, ws_width_m=hi - lo,
                                   slope=rec["slope"], slope_note=rec["slope_note"], n_channel=rec.get("n_channel", fr.N_CHANNEL),
                                   qb_cfs=QB[bid], bank_ft=round(fr.stage_from_q(Q2[bid], table), 2), q2_cfs=Q2[bid],
                                   bank_section_ft=_bank_ft(elev, lo, hi, thal), thr_registry_plus_bed_ft=round(THR_REGISTRY[bid] + BED_BELOW_WS, 2),
                                   watch_note="WATCH = the stage this rating gives at the 2-yr regression flow, so the countdown agrees with the "
                                              "posture rule (WATCH at >= 2-yr on these reaches); the section's own bank and the registry bankfull "
                                              "(+1 ft unseen bed) are kept as checks" + (" - Mountain's registry bankfull is still a placeholder" if bid == "CC-UP-503" else ""),
                                   geom=rec["geom"], table=table)
    RATING_JSON.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    return out


def depth_from_q(bid, q, ratings=None):
    if ratings is None:
        ratings = json.loads(RATING_JSON.read_text(encoding="utf-8"))
    return fr.stage_from_q(q, ratings["reaches"][bid]["table"])


if __name__ == "__main__":
    import cwm_model as cwm
    o = build()
    print(f"{'reach':14s} {'ws_w':>5s} {'WATCH':>6s} {'bank1':>6s} {'reg+1':>6s} {'d@qb':>5s} {'d@10yr':>7s}  (old rect d@qb / d@2yr)")
    for bid, r in o["reaches"].items():
        rq = cwm.BASINS[bid]["reg_q"]
        q2, q10 = rq[0.50], rq[0.10]
        old_qb = cwm.stage_total(0, bid)
        old_2 = cwm.stage_total(q2 - QB[bid], bid) if False else cwm.rect_depth(q2, cwm.BASINS[bid]["sec"])
        print(f"{bid:14s} {r['ws_width_m']:5d} {r['bank_ft']:6.2f} {r['bank_section_ft']:6.2f} {r['thr_registry_plus_bed_ft']:6.2f} {depth_from_q(bid, QB[bid], o):5.2f} "
              f"{depth_from_q(bid, q10, o):7.2f}  ({old_qb:.2f} / {old_2:.2f})")
