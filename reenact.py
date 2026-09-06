#!/usr/bin/env python3
"""
reenact.py — run the engine through a historical storm's rain record, hour by hour.

The What-if replay on storm_watch.html and the Helene recall on live.html need more than
"cumulative rain against a trip line": they need the engine itself run the way it would
have run live. This takes each storm in data/storm_records.json (ERA5 reanalysis rain over
the watershed, antecedent wetness from the same record), and for every basin produces the
modeled hydrograph — real hyetograph → continuous-CN runoff → unit hydrograph → calibrated
peak → total stage — sampled hourly, with the deployed posture at every hour and the hour
each rung was first reached. That is the same chain as cwm_model.assess_event and
live.html's assessBasinEvent, kept as a time series instead of a peak.

    python reenact.py            -> rewrites the "hydro" block of data/storm_records.json

Wetness: one CN per storm, from the record's wetness at the start of the day the main rain
began (first hour the cumulative passes 25 % of the storm total). Helene starts at 0.11 and
the predecessor rain saturates the ground before the main rain, so the day-3 value is what
the main rain meets. Stated in the output.

Caveats travel with the data: one reanalysis cell for all eight basins, ERA5 under-catches
mountain rain, the calibration is the regression fit, and the campus observed peak (8.4 ft,
WATCH) is the only ground truth — the engine over-calls it, which is the point of showing it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cwm_model as cwm
from flood_rating import calibrate_peak

DT = cwm.DT                      # 0.25 h
OBSERVED = {                     # the ground truth we have; the engine is compared to it on the page
    "Helene 2024": {"CC-WCU-2260": {"stage_ft": 8.4, "posture": "WATCH", "source": "observed campus stage (project backtest); the engine over-calls it"}},
}


def main_rain_day(cum: list[float]) -> int:
    tot = cum[-1] if cum else 0.0
    if tot <= 0:
        return 0
    for h, c in enumerate(cum):
        if c >= 0.25 * tot:
            return h // 24
    return 0


def hydrograph(bid: str, hourly_in: list[float], wetness: float) -> dict:
    b = cwm.BASINS[bid]
    CN = cwm.cn_from_wetness(b["CN2"], wetness)
    hy = cwm.real_hyetograph(hourly_in, dt=DT)
    incr, _totQ, _totP = cwm.incremental_runoff(hy, CN)
    uh = cwm.unit_hydrograph(b["DA"], b["Tc"] / 60.0, dt=DT)
    h = [0.0] * (len(incr) + len(uh))
    for i, r in enumerate(incr):
        if r <= 0:
            continue
        for j, u in enumerate(uh):
            h[i + j] += r * u
    raw_pk = max(h) if h else 0.0
    cp = calibrate_peak(raw_pk, bid)
    sc = (cp / raw_pk) if raw_pk > 0 else 0.0
    cal = [x * sc for x in h]
    per = int(round(1.0 / DT))
    hours = list(range(0, len(cal), per))
    stage = []
    post = []
    for k in hours:
        cq = cal[k]
        st = cwm.stage_total(cq, bid)
        stage.append(None if st is None else round(st, 2))
        post.append(cwm._posture_as_deployed(cq, st, bid))
    firsts = {}
    for rung in ("WATCH", "WARNING", "EMERGENCY"):
        idx = next((i for i, p in enumerate(post) if cwm.SEV.get(p, 0) >= cwm.SEV.get(rung, 99)), None) if hasattr(cwm, "SEV") else None
        if idx is None:
            order = {"NORMAL": 1, "WATCH": 2, "WARNING": 3, "EMERGENCY": 4}
            idx = next((i for i, p in enumerate(post) if order.get(p, 0) >= order[rung]), None)
        firsts[rung] = idx
    pk_i = max(range(len(cal)), key=lambda i: cal[i]) if cal else 0
    return dict(CN=round(CN, 1), peak_cfs=round(cp, 0), peak_hr=round(pk_i * DT, 1),
                peak_stage_ft=(None if b["rating"] == "none" else round(cwm.stage_total(cp, bid) or 0, 2)),
                stage_ft=stage, posture=post, first=firsts)


def build(rec_path: Path = Path("data/storm_records.json")) -> dict:
    d = json.loads(rec_path.read_text(encoding="utf-8"))
    for name, s in d["storms"].items():
        cum = s["cum_in"]
        hourly = [round(cum[0], 3)] + [round(cum[i] - cum[i - 1], 3) for i in range(1, len(cum))]
        day = main_rain_day(cum)
        w = s["wetness_by_day"][min(day, len(s["wetness_by_day"]) - 1)]
        basins = {bid: hydrograph(bid, hourly, w) for bid in cwm.BASINS}
        gate = s.get("gateH")
        notice = {}
        for bid, hb in basins.items():
            fw = hb["first"]["WATCH"]
            notice[bid] = None if (fw is None or gate is None) else round(fw - gate, 1)   # + = WATCH after the gate crossing
        s["hydro"] = dict(engine="cwm_model real-hyetograph chain (assess_event as a time series)",
                          wetness_used=w, wetness_day=day + 1, dt_hr=1.0, gateH=gate,
                          basins=basins, notice_hr=notice, observed=OBSERVED.get(name, {}))
    d["hydro_note"] = ("hydro.basins[bid].stage_ft / posture are hourly from replay hour 0; first.* = hour each rung was first "
                       "reached; notice_hr = first WATCH minus the gate-crossing hour (negative = WATCH before the track "
                       "crossed the gate). One CN per storm from the record wetness on the day the main rain began.")
    rec_path.write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
    return d


if __name__ == "__main__":
    d = build(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/storm_records.json"))
    for name, s in d["storms"].items():
        h = s["hydro"]
        wcu = h["basins"]["CC-WCU-2260"]; spd = h["basins"]["CC-SPD-1830"]
        print(f"{name:13s} w={h['wetness_used']:.2f} (day {h['wetness_day']})  campus peak {wcu['peak_stage_ft']} ft "
              f"{max(wcu['posture'], key=lambda p: {'NORMAL':1,'WATCH':2,'WARNING':3,'EMERGENCY':4}.get(p,0))} at T+{wcu['peak_hr']} h · "
              f"first WATCH T+{wcu['first']['WATCH']} (gate T+{h['gateH']}) · Speedwell peak {spd['peak_stage_ft']} ft")
