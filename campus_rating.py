#!/usr/bin/env python3
"""
campus_rating.py — water depth above the bed at the campus warning point, CC-WCU-2260.

The campus POSTURE ladder (7 / 9 / 11 ft, 11 = water in the road) is a stage in the TVA road-datum frame
and stays exactly as validated. What was missing is the plain water depth for the stream-depth column:
the engine's only campus geometry was a regional-regression rectangle (60 ft wide, basins.py "ref only"),
which read 0.5 ft at today's flow — shallower than Speedwell, for a reach that is more confined. This
replaces it with the real channel: a 3DEP LiDAR section at the pour point (~36 ft wide at the water
surface, west bench ~7 ft up, east bank rising 15 ft), water-surface slope 0.0036 along the thalweg,
conveyance Manning (n 0.045 channel / 0.08 overbank, parabolic unseen bed 1 ft below the LiDAR water).
Depth above the inferred thalweg. Same method as fiman_rating.py and mouth_rating.py, same +-35 %.

    python campus_rating.py   -> writes data/campus_rating.json
"""
from __future__ import annotations

import json
from pathlib import Path

import fiman_rating as fr

HERE = Path(__file__).resolve().parent
SECTION_JSON = HERE / "data" / "campus_section_3dep.json"
RATING_JSON = HERE / "data" / "campus_rating.json"
SLOPE = 0.0036
BED_BELOW_WS = 1.0
WS_THRESH = 0.8    # ft above the lowest LiDAR return counted as water: the campus water surface is a shallow riffle with
                   # exposed bars, so the 0.35 used at the gage pool picks a 2 m sliver; 0.8 recovers the ~8 m (26 ft) wetted width
SECTION = "b"      # the three sections agree within ~1 ft; b (24 m downstream) is the middle one
QB_CFS = 45.2      # cwm_model baseflow at campus


def _section(name=SECTION):
    d = json.loads(SECTION_JSON.read_text(encoding="utf-8"))
    elev = list(d["sections"][name]["elev_ft"])
    sta = [float(i) * 3.28084 for i in d["station_m"]]
    mn = min(elev)
    idx = [i for i, v in enumerate(elev) if v <= mn + WS_THRESH]
    lo, hi = min(idx), max(idx)
    c = (lo + hi) / 2.0
    half = (hi - lo) / 2.0 or 1.0
    for i in range(lo, hi + 1):
        elev[i] = mn - BED_BELOW_WS * (1.0 - ((i - c) / half) ** 2)
    return sta, elev, (sta[lo] - 15.0, sta[hi] + 15.0), mn


def build(step=0.1, dmax=16.0):
    sta, elev, ch, ws = _section()
    thal = min(elev)
    table = []
    d = 0.0
    while d <= dmax + 1e-9:
        table.append([round(d, 1), round(fr.conveyance_q(sta, elev, thal + d, slope=SLOPE, ch_range=ch), 1)])
        d += step
    out = dict(reach="CC-WCU-2260 (campus warning point)", built="2026-09-06", thalweg_ft=round(thal, 2), lidar_ws_ft=ws,
               slope=SLOPE, bed_below_ws_ft=BED_BELOW_WS, qb_cfs=QB_CFS, n_channel=fr.N_CHANNEL, n_overbank=fr.N_OVERBANK,
               uncertainty=fr.UNCERTAINTY,
               method="conveyance-weighted Manning through a 3DEP LiDAR section at the pour point; depth above the inferred thalweg. "
                      "Depth only - the posture ladder (7/9/11 ft, TVA road-datum frame) is unchanged.",
               check="this section, run independently of TVA, gives 8.6 ft at the 10-yr flow and 11.5 ft at the 100-yr flow against the "
                     "validated ladder stages 8.7 / 11.0 (three sections bracket 7.3-9.1 / 10.2-12.0): the campus ladder reads as depth above the bed to within ~1 ft",
               table=table)
    RATING_JSON.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    return out


def depth_from_q(q, table=None):
    if table is None:
        table = json.loads(RATING_JSON.read_text(encoding="utf-8"))["table"]
    return fr.stage_from_q(q, table)


if __name__ == "__main__":
    o = build()
    print("campus rating:", len(o["table"]), "rows; thalweg", o["thalweg_ft"], "; depth at qb", round(depth_from_q(QB_CFS), 2),
          "ft; at 57 cfs", round(depth_from_q(57), 2), "; at 2283 (Helene engine)", round(depth_from_q(2283), 2),
          "; at 2580 (10-yr)", round(depth_from_q(2580), 2), "; at 5155 (100-yr)", round(depth_from_q(5155), 2))
