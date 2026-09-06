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

Validated forcing: a storm may carry a "forcing" block naming a gauge-based hyetograph and an
antecedent wetness that the project has validated. Helene does — the K24A hourly hyetograph
scaled to 10 in (k24a_helene_hyeto_scaled.csv) at wetness 0.25, the forcing backtest_helene.py
runs and the one that reproduces the five NCGS surveyed marks to within half a foot. When the
block is present the rain is spliced into the ERA5 window at its timestamps (ERA5 stays on the
days before and after) and the stated wetness is used, so the recall on live.html and the
replay on storm_watch.html are the validated event, not a second, unvalidated one from a
reanalysis cell (ERA5 reads 9.2 in and puts the main rain on a drier soil, giving 8.0 ft —
close, but not the number the marks checked). The ERA5 cumulative is kept as cum_in_era5.

Wetness otherwise: one CN per storm, from the record's wetness at the start of the day the main rain
began (first hour the cumulative passes 25 % of the storm total). Helene starts at 0.11 and
the predecessor rain saturates the ground before the main rain, so the day-3 value is what
the main rain meets. Stated in the output.

Caveats travel with the data: one reanalysis cell for all eight basins, ERA5 under-catches
mountain rain, the calibration is the regression fit. Comparisons: the campus 8.4 ft is
marks-implied (consistent, not independent); the Speedwell FIMAN rise of 6.36 ft is the only
measured number, and the rating there is known ~2x too shallow.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cwm_model as cwm
from flood_rating import calibrate_peak

DT = cwm.DT                      # 0.25 h
OBSERVED = {                     # what we actually have to compare against, stated honestly
    "Helene 2024": {
        # Campus has no gauge. 8.4 ft is the peak the NCGS surveyed high-water marks (~10-yr, on the
        # Speedwell-to-campus reach) imply through the reach rating — see HELENE_RETURN_PERIOD_CONFLICT.
        # It is NOT an independent measurement of this engine (it was derived with the same real-hyetograph
        # chain from the marks), so agreement here means "consistent", not "validated".
        "CC-WCU-2260": {"stage_ft": 8.4, "posture": "WATCH", "kind": "marks-implied",
                        "source": "peak implied by the NCGS surveyed Helene marks (~10-yr) through the reach rating; no gauge at campus"},
        # Speedwell has the only measured record: FIMAN 25380 read 8.86 ft gage height at the Helene peak
        # against 2.50 ft at low flow - a 6.36 ft rise, which needs no datum. The rectangular rating used
        # here is documented ~2x too shallow (noah_rating_error_quantified_2026-08-07), so the modeled rise
        # under-predicts it. That gap is a known rating problem, not a rainfall problem.
        "CC-SPD-1830": {"rise_ft": 6.36, "gage_peak_ft": 8.86, "gage_low_ft": 2.50, "kind": "measured",
                        "source": "FIMAN 25380 measured rise, Helene peak minus low flow (datum-free); the rating here is ~2x too shallow"},
    },
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
    rise = None if (not stage or stage[0] is None or b["rating"] == "none") else round((cwm.stage_total(cp, bid) or 0) - stage[0], 2)
    return dict(CN=round(CN, 1), peak_cfs=round(cp, 0), peak_hr=round(pk_i * DT, 1), rise_ft=rise,
                peak_stage_ft=(None if b["rating"] == "none" else round(cwm.stage_total(cp, bid) or 0, 2)),
                stage_ft=stage, posture=post, first=firsts)


VALIDATED_FORCING = {
    "Helene 2024": dict(
        rain_csv="k24a_helene_hyeto_scaled.csv", rain_total_in=10.0, wetness=0.25,
        source="K24A hourly hyetograph scaled to the 10 in basin total (peak 0.66 in/h), antecedent wetness 0.25 "
               "(drought-dry) — the backtest_helene.py forcing, which reproduces the five NCGS surveyed Helene "
               "high-water marks to within 0.3-0.5 ft (HELENE_DECISION, 2026-07-31)"),
}


def _splice_forcing(s: dict, name: str, base: Path) -> dict:
    """Replace the ERA5 hourly rain with the validated gauge hyetograph over its own hours; keep ERA5 outside."""
    f = VALIDATED_FORCING.get(name)
    if not f:
        return s
    import csv
    from datetime import datetime, timezone
    era = s.get("cum_in_era5") or s["cum_in"]
    hourly = [era[0]] + [era[i] - era[i - 1] for i in range(1, len(era))]
    t0 = datetime.fromisoformat(s["t0"].replace("Z", "+00:00"))
    rows = list(csv.reader((base / f["rain_csv"]).read_text(encoding="utf-8").splitlines()))
    rows = [r for r in rows if len(r) >= 2 and r[0][:2] == "20"]
    first = last = None
    for ts, v in rows:
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        h = int(round((t - t0).total_seconds() / 3600.0))
        if 0 <= h < len(hourly):
            hourly[h] = float(v)
            first = h if first is None else first
            last = h
    # the gauge record covers the whole event: zero ERA5 inside its span (already overwritten hour by hour)
    cum, run = [], 0.0
    for v in hourly:
        run += v
        cum.append(round(run, 3))
    s["cum_in_era5"] = era
    s["cum_in"] = cum
    s["total_in"] = round(cum[-1], 2)
    # antecedent by day: start from the validated wetness (inverted through the same API map) and carry the
    # spliced rain forward day by day, so the card's wetness and trip lines follow the validated event too
    import wetness as W
    import readiness as R
    month = t0.month
    lo = (1.4 if W.is_growing_season(month) else 0.5) * W.API_5DAY_EQUIV
    hi = (2.1 if W.is_growing_season(month) else 1.1) * W.API_5DAY_EQUIV
    w0 = float(f["wetness"])
    api = (w0 / 0.5) * lo if w0 < 0.5 else lo + (w0 - 0.5) / 0.5 * (hi - lo)
    s["api30_in_era5"] = s.get("api30_in_era5", s["api30_in"])
    s["api30_in"] = round(api, 2)
    ndays = len(s["wetness_by_day"])
    s["wetness_by_day_era5"] = s.get("wetness_by_day_era5") or s["wetness_by_day"]
    wbd = []
    for d_ in range(ndays):
        wbd.append(round(W.wetness_from_api(api, month), 3))
        api = W.API_K * api + sum(hourly[d_ * 24:(d_ + 1) * 24])
    s["wetness_by_day"] = wbd
    s["trip_by_day"] = [{bid: R.trip_inches(bid, w_) for bid in cwm.BASINS} for w_ in wbd]
    s["wetness"] = wbd[0]
    s["forcing"] = dict(rain=f["source"], rain_csv=f["rain_csv"], hours=[first, last], wetness=f["wetness"],
                        era5_total_in=round(era[-1], 2))
    return s


def build(rec_path: Path = Path("data/storm_records.json")) -> dict:
    d = json.loads(rec_path.read_text(encoding="utf-8"))
    for name, s in d["storms"].items():
        _splice_forcing(s, name, rec_path.resolve().parent.parent)
        cum = s["cum_in"]
        hourly = [round(cum[0], 3)] + [round(cum[i] - cum[i - 1], 3) for i in range(1, len(cum))]
        day = main_rain_day(cum)
        w = s["wetness_by_day"][min(day, len(s["wetness_by_day"]) - 1)]
        if s.get("forcing") and s["forcing"].get("wetness") is not None:
            w = float(s["forcing"]["wetness"])
        basins = {bid: hydrograph(bid, hourly, w) for bid in cwm.BASINS}
        gate = s.get("gateH")
        notice = {}
        for bid, hb in basins.items():
            fw = hb["first"]["WATCH"]
            notice[bid] = None if (fw is None or gate is None) else round(fw - gate, 1)   # + = WATCH after the gate crossing
        s["hydro"] = dict(engine="cwm_model real-hyetograph chain (assess_event as a time series)",
                          wetness_used=w, wetness_day=day + 1, dt_hr=1.0, gateH=gate,
                          wetness_source=("validated antecedent (" + s["forcing"]["rain"].split(",")[0] + ")" if s.get("forcing") else "ERA5 30-day API at the start of the main-rain day"),
                          rain_source=(s["forcing"]["rain"] if s.get("forcing") else "ERA5 reanalysis, one cell over the watershed (Open-Meteo archive)"),
                          basins=basins, notice_hr=notice, observed=OBSERVED.get(name, {}))
    d["hydro_note"] = ("hydro.basins[bid].stage_ft / posture are hourly from replay hour 0; first.* = hour each rung was first "
                       "reached; notice_hr = first WATCH minus the gate-crossing hour (negative = WATCH before the track "
                       "crossed the gate). One CN per storm from the record wetness on the day the main rain began, or the "
                       "validated antecedent where a storm carries a 'forcing' block (Helene: K24A 10 in at wetness 0.25, "
                       "the backtest forcing that matches the NCGS marks; cum_in is then that hyetograph spliced into the "
                       "ERA5 window and cum_in_era5 keeps the reanalysis).")
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
