#!/usr/bin/env python3
"""
mouth_rating.py — a stage-discharge rating for the Cullowhee (mouth) reach, CC-MOUTH-2340.

The mouth has had rating="none" because the last few hundred metres of Cullowhee Creek are
backwater-controlled by the Tuckasegee: in Helene the river stood ~27 ft above its low-flow
surface at the confluence (WCU HWM at Bellamy 2079.3 ft vs river 2051.8), and no creek rating
can represent that. But the reach ~250 m above the mouth has its own channel (3DEP LiDAR
section: ~26 ft wide at the water surface, banks ~4 ft, right-bank floodplain at ~+3.5 ft,
water-surface slope 0.004), and at ordinary flows the depth there IS the creek's depth.

So: a rating for the creek's own flow, built the same way as fiman_rating.py (conveyance
Manning through the LiDAR section, parabolic unseen bed 1 ft below the LiDAR water surface,
n 0.045 channel / 0.08 overbank, slope 0.004). Depth is above the inferred thalweg. It is
valid while the Tuckasegee is below this reach's banks; live.html labels it "creek only —
backwater not represented". Uncertainty as fiman_rating (~+-35 %).

    python mouth_rating.py    -> writes data/mouth_rating.json
"""
from __future__ import annotations

import json
from pathlib import Path

import fiman_rating as fr

HERE = Path(__file__).resolve().parent
SECTION_JSON = HERE / "data" / "mouth_section_3dep.json"
RATING_JSON = HERE / "data" / "mouth_rating.json"
SLOPE = 0.004
BED_BELOW_WS = 1.0
QB_CFS = 47.0      # baseflow at the mouth: campus 45.2 cfs scaled by 23.4/22.6
BANK_SECTION_FT = 4.8   # the right-bank bench in the section, ~4.8 ft above the thalweg (check value)
Q2_CFS = 1030.0         # StreamStats 2-yr at the mouth: WATCH rung = stage at this flow, consistent with the >=2-yr posture rule


def _section(name="a"):
    d = json.loads(SECTION_JSON.read_text(encoding="utf-8"))
    elev = list(d["sections"][name]["elev_ft"])
    sta = [float(i) * 3.28084 for i in d["station_m"]]
    mn = min(elev)
    idx = [i for i, v in enumerate(elev) if v <= mn + 0.25]
    lo, hi = min(idx), max(idx)
    c = (lo + hi) / 2.0
    half = (hi - lo) / 2.0 or 1.0
    for i in range(lo, hi + 1):
        elev[i] = mn - BED_BELOW_WS * (1.0 - ((i - c) / half) ** 2)
    return sta, elev, (sta[lo] - 15.0, sta[hi] + 15.0), mn


def build(step=0.1, dmax=14.0):
    sta, elev, ch, ws = _section("a")
    thal = min(elev)
    table = []
    d = 0.0
    while d <= dmax + 1e-9:
        table.append([round(d, 1), round(fr.conveyance_q(sta, elev, thal + d, slope=SLOPE, ch_range=ch), 1)])
        d += step
    out = dict(reach="CC-MOUTH-2340 (Cullowhee Creek ~250 m above the Tuckasegee)", built="2026-09-06",
               thalweg_ft=round(thal, 2), lidar_ws_ft=ws, slope=SLOPE, bed_below_ws_ft=BED_BELOW_WS, qb_cfs=QB_CFS,
               bank_ft=round(fr.stage_from_q(Q2_CFS, table), 2), q2_cfs=Q2_CFS, bank_section_ft=BANK_SECTION_FT,
               watch_note="WATCH = stage at the 2-yr regression flow through this rating (the section's bench is at 4.8 ft, a check)",
               n_channel=fr.N_CHANNEL, n_overbank=fr.N_OVERBANK, uncertainty=fr.UNCERTAINTY,
               method="conveyance-weighted Manning through a 3DEP LiDAR section; depth above the inferred thalweg; creek flow only",
               caveat="Tuckasegee backwater is NOT represented: in Helene the river rose ~27 ft at the confluence and drowned this reach. "
                      "Valid while the river is below the reach's banks (~4 ft above low-flow surface).",
               table=table)
    RATING_JSON.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    return out


def depth_from_q(q, table=None):
    if table is None:
        table = json.loads(RATING_JSON.read_text(encoding="utf-8"))["table"]
    return fr.stage_from_q(q, table)


if __name__ == "__main__":
    o = build()
    print("mouth rating:", len(o["table"]), "rows; thalweg", o["thalweg_ft"], "ft;",
          "depth at qb", round(depth_from_q(QB_CFS), 2), "ft; at 1000 cfs", round(depth_from_q(1000), 2),
          "ft; at 2450 (10-yr)", round(depth_from_q(2450), 2), "ft; at 3900 (Helene AR)", round(depth_from_q(3900), 2))
