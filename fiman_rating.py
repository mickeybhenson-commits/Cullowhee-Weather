#!/usr/bin/env python3
"""
fiman_rating.py — a stage-discharge rating for NCEM FIMAN 25380 (Cullowhee Creek at Speedwell),
and the drainage-area-ratio transfer that turns one measured stage into a flow on every reach.

WHY. FIMAN publishes gage height at Speedwell and nothing else: no flow sensor, no rating
(FLOW_SENSOR_ID null, CURRENT_FLOW null; NWPS has no rating for CUCN7 either). The engine's
own rectangular rating for that reach is documented ~2x too shallow. Until the FRESHET stage
nodes report, the honest way to use the one measurement in the watershed is:

    gage height  --rating-->  discharge at Speedwell  --area ratio-->  discharge on every reach
                                                      --each reach's rating-->  depth there

THE RATING (built 2026-09-06). Cross-section cut from USGS 3DEP 1 m (NC QL2 LiDAR) through the
FIMAN site point, perpendicular to the channel (data/fiman_25380_section_3dep.json). LiDAR
returns the water surface in the channel, not the bed: the unseen part is filled with a parabolic
bottom BED_BELOW_WS ft below the LiDAR water surface (2127.1 ft, i.e. gage height ~2.1 ft at
survey time, against 2.47-2.50 ft at low flow now — the same water). Discharge by conveyance-
weighted Manning: channel n 0.045 (cobble mountain creek), overbank n 0.08, water-surface slope
0.006 measured along the thalweg on a 3 m LiDAR grid from 40 m upstream to 60 m downstream of
the gage (the reach steepens to 0.011 upstream of the gage pool and flattens to 0.004 below;
the band is the stated uncertainty). Check points: 2.47 ft -> ~45 cfs (engine baseflow 36.6);
Helene peak 8.86 ft -> ~3,100 cfs (~40-yr by StreamStats for 18.3 mi2; 2,500 at S=0.004).

What that check says, plainly: the engine's Helene peak at Speedwell (1,600 cfs, ~6-yr) is low
against the gage read through this rating (2,500-3,100 cfs, ~20-40-yr). The NCGS marks downstream
sit 0.3-0.5 ft ABOVE the effective 10-yr water surface, which on those flat sections is also more
than 10-yr, so the two measurements agree with each other and both say the engine under-predicts
Speedwell. That is the correction this module lets live.html carry.

TIER. Everything derived here is DERIVED FROM A MEASUREMENT, not a measurement: it may sharpen
a WATCH and inform the stream-depth column; it does not by itself assert WARNING anywhere.
Uncertainty ~ +-35 % on discharge from the slope band and the unseen bed; the area-ratio step
assumes uniform runoff per square mile, which holds at baseflow and for slow uniform rain and
weakens in a storm (orographic tilt, different lags).

    python fiman_rating.py            -> writes data/fiman_25380_rating.json, prints checks
"""
from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECTION_JSON = HERE / "data" / "fiman_25380_section_3dep.json"
RATING_JSON = HERE / "data" / "fiman_25380_rating.json"

GAGE_DATUM_FT = 2125.0        # FIMAN GAGE_DATUM (contested vs NWS by 1.0 ft; irrelevant here — rating is in gage height)
SLOPE = 0.006                 # water-surface slope along the thalweg at the gage (3DEP grid); band 0.004-0.011
N_CHANNEL = 0.045
N_OVERBANK = 0.08
BED_BELOW_WS = 1.0            # ft of unseen channel below the LiDAR water surface
UNCERTAINTY = 0.35            # fractional, on discharge
DA_SPEEDWELL = 18.3           # mi2 (StreamStats)
DA = {"CC-UP-503": 5.03, "CC-MS-1100": 11.0, "CC-TIL-705": 7.05, "CC-SPD-1830": 18.3,
      "CC-COX-097": 0.97, "CC-LB-171": 1.71, "CC-WCU-2260": 22.6, "CC-MOUTH-2340": 23.4}


def _section(name: str = "gage"):
    d = json.loads(SECTION_JSON.read_text(encoding="utf-8"))
    elev = list(d["sections"][name]["elev_ft"])
    sta = [float(i) * 3.28084 for i in d["station_m"]]
    mn = min(elev)
    idx = [i for i, v in enumerate(elev) if v <= mn + 0.35]
    lo, hi = min(idx), max(idx)
    c = (lo + hi) / 2.0
    half = (hi - lo) / 2.0 or 1.0
    for i in range(lo, hi + 1):
        elev[i] = mn - BED_BELOW_WS * (1.0 - ((i - c) / half) ** 2)
    return sta, elev, (sta[lo] - 20.0, sta[hi] + 20.0), mn


def conveyance_q(sta, elev, wse, slope=SLOPE, ch_range=None, n_ch=N_CHANNEL, n_ob=N_OVERBANK) -> float:
    q = 0.0
    for i in range(len(sta) - 1):
        x0, x1, z0, z1 = sta[i], sta[i + 1], elev[i], elev[i + 1]
        d0, d1 = max(0.0, wse - z0), max(0.0, wse - z1)
        if d0 <= 0 and d1 <= 0:
            continue
        w = x1 - x0
        if d0 > 0 and d1 > 0:
            a = w * (d0 + d1) / 2.0
            p = math.hypot(w, z1 - z0)
        else:
            frac = min(1.0, max(d0, d1) / (abs(d1 - d0) or 1e-9))
            a = 0.5 * w * frac * max(d0, d1)
            p = math.hypot(w * frac, abs(z1 - z0) * frac)
        r = a / p if p > 0 else 0.0
        n = n_ch if (ch_range and ch_range[0] <= x0 <= ch_range[1]) else n_ob
        q += (1.49 / n) * a * r ** (2.0 / 3.0) * math.sqrt(slope)
    return q


def build(step: float = 0.1, hmax: float = 16.0) -> dict:
    sta, elev, ch, ws = _section("gage")
    table = []
    h = 0.0
    while h <= hmax + 1e-9:
        q = conveyance_q(sta, elev, GAGE_DATUM_FT + h, ch_range=ch)
        table.append([round(h, 1), round(q, 1)])
        h += step
    out = dict(
        site="NCEM FIMAN 25380 (CUCN7) Cullowhee Creek at Speedwell", built="2026-09-06",
        gage_datum_ft=GAGE_DATUM_FT, slope=SLOPE, slope_band=[0.004, 0.011], n_channel=N_CHANNEL, n_overbank=N_OVERBANK,
        bed_below_ws_ft=BED_BELOW_WS, lidar_ws_ft=ws, uncertainty=UNCERTAINTY, da_sqmi=DA_SPEEDWELL, da=DA,
        method="conveyance-weighted Manning through a 3DEP (NC QL2 LiDAR, 1 m) cross-section at the FIMAN site point; "
               "parabolic unseen bed below the LiDAR water surface; discharge in cfs against gage height in ft",
        tier="derived from a measurement (not a measurement): informs the stream-depth column and can sharpen a WATCH; "
             "never asserts WARNING by itself",
        table=table,
        checks={"h_2.47_low_flow": round(q_from_stage(2.47, table)), "h_8.86_helene_peak": round(q_from_stage(8.86, table)),
                "streamstats_10yr": 2010, "streamstats_25yr": 2740, "streamstats_50yr": 3410, "engine_helene_peak": 1603},
    )
    RATING_JSON.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    return out


def q_from_stage(h: float, table=None) -> float:
    """Discharge (cfs) for a FIMAN gage height (ft), linear between table rows."""
    if table is None:
        table = json.loads(RATING_JSON.read_text(encoding="utf-8"))["table"]
    if h <= table[0][0]:
        return table[0][1]
    for (h0, q0), (h1, q1) in zip(table, table[1:]):
        if h0 <= h <= h1:
            return q0 + (q1 - q0) * (h - h0) / (h1 - h0)
    return table[-1][1]


def stage_from_q(q: float, table=None) -> float:
    if table is None:
        table = json.loads(RATING_JSON.read_text(encoding="utf-8"))["table"]
    for (h0, q0), (h1, q1) in zip(table, table[1:]):
        if q0 <= q <= q1:
            return h0 + (h1 - h0) * (q - q0) / ((q1 - q0) or 1e-9)
    return table[-1][0]


def transfer(q_speedwell: float) -> dict:
    """Drainage-area-ratio transfer of a Speedwell discharge to every reach (cfs)."""
    return {bid: q_speedwell * da / DA_SPEEDWELL for bid, da in DA.items()}


if __name__ == "__main__":
    out = build()
    print(f"rating: {len(out['table'])} rows to {out['table'][-1][0]} ft; checks {out['checks']}")
    q = q_from_stage(2.47)
    print("2.47 ft ->", round(q), "cfs;", {k: round(v) for k, v in transfer(q).items()})
