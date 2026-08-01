#!/usr/bin/env python3
"""
forecast.py - LIVE per-basin flood forecast for the Cullowhee Creek watershed.

This is the integration layer that was missing: the validated science core
(basins.py registry -> cwm_model chain -> flood_rating engine) and the live data
connectors (Open-Meteo QPF, wetness.py antecedent state) both existed, but
nothing joined them, so feed_runner.get_modeled_stage_ft() returned None and the
published feed carried no forecast at all.

CHAIN PER BASIN (all eight nodes, every run)
  live rainfall  -> QPF: rolling 24-h maximum over the forecast window
                    (matches the 24-h SCS Type II design hyetograph the engine
                    integrates; a 3-day sum poured into a 24-h storm would
                    invent intensity that no forecast called)
  live rainfall  -> antecedent: 30-day decayed API -> wetness index w in [0,1]
                    (wetness.resolve_wetness ladder; soil percentile out-ranks
                    API when a soil feed is supplied)
  w + CN2        -> continuous curve number (wetness.cn_from_wetness)
  QPF + CN       -> SCS hyetograph -> NRCS-CN runoff -> unit hydrograph -> raw
                    peak Q                                    (cwm_model)
  raw peak Q     -> per-basin regression calibration -> return period -> POSTURE
                    + USGS 90% prediction-interval confidence band  (flood_rating)
  calibrated Q   -> baseflow-inclusive total stage               (wetness)
  QPF x wetness  -> 3x3 input-uncertainty ensemble -> posture distribution
                                                                 (flood_ensemble)
  basins.py Tc   -> lead time vs the 120-min operational requirement (lead_time)

PROVENANCE / AUTHORITY
  Every posture here is MODELED. NWS (WFO GSP) is the warning authority and NCEM
  FIMAN is the authoritative gage record; this is a forecast overlay that sits
  alongside them. Forecast QPF is known to under-call orographic mountain
  rainfall, so `qpf_bias_note` stays attached to every record and the watershed
  roll-up is reported in shadow mode.

  Nothing here fabricates a number. If the rainfall fetch fails, forecast_all()
  is simply not called and run() returns {"ok": False, "error": ...} - a missing
  forecast is a legitimate answer, a plausible fake one is the failure mode this
  whole design exists to avoid.

USAGE
  python forecast.py                  # live run, printed table (needs network)
  python forecast.py --demo 4.8 0.5   # offline: force QPF=4.8 in, wetness=0.50

  from forecast import run, forecast_all, forcing_from_response
  run()                               # -> full forecast dict, network
  forecast_all({bid: {"qpf_in": ..., "wetness": ...}})   # pure, no network
"""

from __future__ import annotations

import datetime
import json
import math
import urllib.parse
import urllib.request

import cwm_model as cwm
import flood_ensemble
import lead_time
import wetness as wet
from basins import BASINS, routed_order
from flood_rating import assess, calibrate_peak

# Basin representative points (lat, lon) - centroids/pour points of each
# sub-basin. Mirrors live_rainfall.BASIN_POINTS.
BASIN_POINTS = {
    "CC-UP-503":     (35.241, -83.185),
    "CC-MS-1100":    (35.265, -83.190),
    "CC-TIL-705":    (35.268, -83.205),
    "CC-SPD-1830":   (35.270, -83.190),
    "CC-COX-097":    (35.302, -83.178),
    "CC-LB-171":     (35.305, -83.195),
    "CC-WCU-2260":   (35.290, -83.185),
    "CC-MOUTH-2340": (35.300, -83.185),
}

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
PAST_DAYS = 30        # spin-up for the 30-day decayed API
FORECAST_DAYS = 3     # QPF search window
QPF_WINDOW_HR = 24    # design-storm duration the engine integrates

SEVERITY = ["NORMAL", "WATCH", "WARNING", "EMERGENCY"]

QPF_BIAS_NOTE = ("Forecast QPF under-calls orographic mountain rainfall; treat "
                 "the modeled posture as a floor, not a ceiling.")


# ---------------------------------------------------------------------------
# NETWORK  (the only function here that touches the internet)
# ---------------------------------------------------------------------------
def fetch_weather(points=BASIN_POINTS, past_days=PAST_DAYS,
                  forecast_days=FORECAST_DAYS, timeout=30):
    """One bulk Open-Meteo call for all eight basins.

    Returns a list of per-point response dicts, in `points` order, each carrying
    `hourly.precipitation` (for the rolling 24-h QPF) and
    `daily.precipitation_sum` (for the 30-day antecedent API), both in inches.
    """
    q = {
        "latitude": ",".join(f"{p[0]}" for p in points.values()),
        "longitude": ",".join(f"{p[1]}" for p in points.values()),
        "hourly": "precipitation",
        "daily": "precipitation_sum",
        "past_days": past_days,
        "forecast_days": forecast_days,
        "precipitation_unit": "inch",
        "timezone": "UTC",
    }
    url = OPEN_METEO + "?" + urllib.parse.urlencode(q, safe=",")
    req = urllib.request.Request(url, headers={"User-Agent": "cullowhee-flood/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return data if isinstance(data, list) else [data]


# ---------------------------------------------------------------------------
# PURE: response -> forcing   (no network; unit-testable)
# ---------------------------------------------------------------------------
def rolling_max(values, width):
    """Maximum sum over any `width`-long contiguous window. Returns
    (max_sum, start_index). Empty/short series fall back to the whole series."""
    vals = [(v or 0.0) for v in values]
    if not vals:
        return 0.0, 0
    if len(vals) <= width:
        return sum(vals), 0
    window = sum(vals[:width])
    best, best_i = window, 0
    for i in range(1, len(vals) - width + 1):
        window += vals[i + width - 1] - vals[i - 1]
        if window > best:
            best, best_i = window, i
    return best, best_i


def _now_index(times, now_iso):
    """Index of the first hourly timestamp at or after `now_iso`."""
    for i, t in enumerate(times):
        if t >= now_iso:
            return i
    return max(0, len(times) - 1)


def forcing_from_response(data, points=BASIN_POINTS, now=None, month=None,
                          soil_pct=None):
    """Per-basin forcing from an Open-Meteo bulk response. Pure.

    Per basin:
      qpf_in        rolling 24-h maximum precipitation over the forecast window
      qpf_start_utc ISO timestamp the controlling 24-h window opens
      api_in        30-day decayed antecedent precipitation index (inches)
      wetness       wetness index in [0,1] driving the continuous curve number
      wetness_src   which rung of the wetness ladder supplied it

    `soil_pct` ({bid: percentile}) is optional and out-ranks the API when given.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:00")
    out = {}
    for (bid, _), loc in zip(points.items(), data):
        hourly = loc.get("hourly", {}) or {}
        times = hourly.get("time", []) or []
        precip = hourly.get("precipitation", []) or []
        daily = (loc.get("daily", {}) or {}).get("precipitation_sum", []) or []

        i0 = _now_index(times, now_iso)
        fut_t, fut_p = times[i0:], precip[i0:]
        qpf, wi = rolling_max(fut_p, QPF_WINDOW_HR)

        # Antecedent: completed days only. The daily series spans past_days +
        # forecast_days; drop the forecast tail so tomorrow's rain cannot leak
        # backwards into today's soil state.
        completed = daily[:-FORECAST_DAYS] if len(daily) > FORECAST_DAYS else daily
        api = wet.api_from_daily(completed)
        w, wsrc = wet.resolve_wetness(
            soil_pct=(soil_pct or {}).get(bid), api_in=api,
            month=month if month is not None else now.month)

        out[bid] = {
            "qpf_in": round(qpf, 2),
            "qpf_window_hr": QPF_WINDOW_HR,
            "qpf_start_utc": (fut_t[wi] if wi < len(fut_t) else None),
            "fcst_total_in": round(sum(v or 0.0 for v in fut_p), 2),
            "api_in": round(api, 2),
            "wetness": round(w, 3),
            "wetness_src": wsrc,
        }
    return out


# ---------------------------------------------------------------------------
# PURE: forcing -> per-basin forecast   (no network; unit-testable)
# ---------------------------------------------------------------------------
def forecast_basin(bid, qpf_in, wetness, with_ensemble=True):
    """Full chain for one basin. Returns the operative posture plus every
    cross-check an operator needs to judge how much to trust it."""
    rec = BASINS[bid]
    cn2 = cwm.BASINS[bid]["CN2"]
    cn = wet.cn_from_wetness(cn2, wetness)

    hyeto = cwm.storm_hyetograph(qpf_in)
    _, runoff_in, _ = cwm.incremental_runoff(hyeto, cn)
    qp_raw = cwm.peak_discharge(hyeto, cn, cwm.BASINS[bid]["DA"],
                                cwm.BASINS[bid]["Tc"] / 60.0)

    # Authoritative engine: it applies the per-basin regression calibration
    # itself, so it must be fed the RAW model peak.
    a = assess(qp_raw, bid)
    calib_q = a["calib_q"]

    # Baseflow-inclusive total stage (thresholds are total stage, not storm-only).
    stage_total = wet.stage_total_from_q(calib_q, bid)
    lead = lead_time.lead_flags(bid)

    rowout = {
        "basin": bid,
        "name": rec["name"],
        "role": rec.get("role"),
        "da_sqmi": rec["da_sqmi"],
        # forcing
        "qpf_in": round(qpf_in, 2),
        "wetness": round(wetness, 3),
        "cn": round(cn, 1),
        "runoff_in": round(runoff_in, 2),
        "runoff_ratio": round(runoff_in / qpf_in, 2) if qpf_in else None,
        # discharge
        "qp_raw_cfs": round(qp_raw),
        "calib_q_cfs": calib_q,
        "baseflow_cfs": wet.baseflow_q(bid),
        # posture (authoritative)
        "posture": a["posture"],
        "basis": a["basis"],
        "rp_best_yr": a.get("rp_best"),
        "rp_band_yr": a.get("rp_band"),
        "confidence": a.get("confidence"),
        # stage
        "rating": a["rating"],
        "storm_depth_ft": a["depth_ft"],
        "stage_total_ft": round(stage_total, 2) if stage_total is not None else None,
        "thr_ft": rec["thr_ft"],
        "thr_validated": a["thr_validated"],
        "stage_posture_xcheck": a["stage_posture"],
        # timing. tc_min is the registry value (drives the lead-time flag);
        # tc_model_min is what the unit hydrograph actually ran. They agree on
        # six of eight reaches. MS-1100 (63 vs 86) and SPD-1830 (62 vs 91) do
        # not, and both are reaches whose basins.py tc_src explicitly records an
        # ambiguous Tc. The engine value is NOT silently reconciled here: the
        # per-basin calibration anchors were fit at the engine Tc and the Helene
        # back-test validates against them, so changing it would invalidate a
        # validated engine. Flagged instead - see test_forecast.py.
        "tc_min": lead["tc_min"],
        "tc_model_min": cwm.BASINS[bid]["Tc"],
        "tc_consistent": lead["tc_min"] == cwm.BASINS[bid]["Tc"],
        "lead_limited": lead["lead_limited"],
        "lead_margin_min": lead["margin_min"],
    }

    if with_ensemble:
        e = flood_ensemble.ensemble(bid, qpf_in, wetness)
        rowout["ensemble_dist"] = e["posture_dist"]
        rowout["ensemble_modal"] = e["modal"]
        rowout["ensemble_firm"] = e["firm"]
    return rowout


def _worst(postures):
    """Highest severity present, ignoring N/A."""
    ranked = [p for p in postures if p in SEVERITY]
    return max(ranked, key=SEVERITY.index) if ranked else "N/A"


def forecast_all(forcing, with_ensemble=True):
    """Every basin plus the watershed roll-up. Pure.

    `forcing` maps basin id -> {"qpf_in": float, "wetness": float, ...}; any
    extra keys (api_in, qpf_start_utc, ...) are carried through onto the record.
    """
    basins_out = {}
    for bid in routed_order():
        f = forcing.get(bid)
        if not f:
            continue
        row = forecast_basin(bid, f["qpf_in"], f["wetness"],
                             with_ensemble=with_ensemble)
        for k in ("api_in", "wetness_src", "qpf_start_utc", "qpf_window_hr",
                  "fcst_total_in"):
            if k in f:
                row[k] = f[k]
        basins_out[bid] = row

    # Roll-up. The mouth is a downstream bookend whose real posture is
    # backwater-controlled by the Tuckasegee (confluence_status.py owns that),
    # so it is reported but excluded from the watershed severity.
    in_scope = [b for b in basins_out if BASINS[b].get("role") != "out_of_scope"]
    worst = _worst([basins_out[b]["posture"] for b in in_scope])
    driving = [b for b in in_scope if basins_out[b]["posture"] == worst]
    campus = basins_out.get("CC-WCU-2260", {})

    watershed = {
        "posture": worst,
        "driving_basins": driving,
        "warning_point": "CC-WCU-2260",
        "warning_point_posture": campus.get("posture"),
        "warning_point_stage_ft": campus.get("stage_total_ft"),
        "warning_point_thr_ft": campus.get("thr_ft"),
        "basins_at_or_above_watch": sorted(
            b for b in in_scope
            if basins_out[b]["posture"] in ("WATCH", "WARNING", "EMERGENCY")),
        "lead_limited_basins": sorted(
            b for b in in_scope if basins_out[b]["lead_limited"]),
        "max_qpf_in": max((basins_out[b]["qpf_in"] for b in basins_out), default=None),
        "shadow_mode": True,
        "qpf_bias_note": QPF_BIAS_NOTE,
        "authority_note": ("Not an official warning product. NWS (WFO GSP) is the "
                           "warning authority; NCEM FIMAN is the authoritative gage "
                           "record."),
    }
    return {"basins": basins_out, "watershed": watershed}


# ---------------------------------------------------------------------------
# LIVE RUN
# ---------------------------------------------------------------------------
def run(points=BASIN_POINTS, now=None, with_ensemble=True, timeout=30):
    """Fetch + compute. Never raises on a network failure: returns
    {"ok": False, "error": ...} so the publisher can emit "No Data" rather than
    a fabricated stage."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        data = fetch_weather(points, timeout=timeout)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "generated_utc": now.isoformat(), "basins": {}, "watershed": None}

    try:
        forcing = forcing_from_response(data, points, now=now)
        out = forecast_all(forcing, with_ensemble=with_ensemble)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "generated_utc": now.isoformat(), "basins": {}, "watershed": None}

    out["ok"] = True
    out["generated_utc"] = now.isoformat()
    out["source"] = "Open-Meteo hourly QPF + 30-day API antecedent (MODELED)"
    out["engine"] = ("cwm_model chain -> flood_rating (USGS SIR 2023-5006 "
                     "regression frequency; TVA stage at campus)")
    return out


def modeled_stage_ft(fc=None, bid="CC-WCU-2260"):
    """Total modeled stage (ft above bed) at the warning point, or None.

    This is the hook feed_runner.get_modeled_stage_ft() needs. Returns None on
    any failure or missing basin - never a placeholder constant.
    """
    fc = fc if fc is not None else run(with_ensemble=False)
    if not fc.get("ok"):
        return None
    return (fc.get("basins", {}).get(bid) or {}).get("stage_total_ft")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def print_report(fc):
    ws = fc.get("watershed")
    print("=" * 108)
    print("CULLOWHEE CREEK WATERSHED - LIVE FLOOD FORECAST (all 8 basins)")
    print(f"  generated {fc.get('generated_utc')}   source: {fc.get('source')}")
    print("=" * 108)
    if not fc.get("ok"):
        print(f"NO FORECAST: {fc.get('error')}")
        print("(a missing forecast is a legitimate answer; no number is invented)")
        return
    hdr = (f"{'basin':15s}{'QPF in':>7}{'wet':>6}{'CN':>6}{'Q cfs':>8}{'RP yr':>7}"
           f"  {'POSTURE':<11}{'confidence':<20}{'stage ft':>9}{'Tc':>5} lead")
    print(hdr)
    print("-" * len(hdr))
    for bid in routed_order():
        r = fc["basins"].get(bid)
        if not r:
            continue
        stage = f"{r['stage_total_ft']:.2f}" if r["stage_total_ft"] is not None else "--"
        rp = r["rp_best_yr"] if r["rp_best_yr"] is not None else "--"
        lead = "LIMITED" if r["lead_limited"] else "ok"
        print(f"{bid:15s}{r['qpf_in']:7.2f}{r['wetness']:6.2f}{r['cn']:6.1f}"
              f"{r['calib_q_cfs']:8d}{str(rp):>7}  {r['posture']:<11}"
              f"{str(r['confidence']):<20}{stage:>9}{r['tc_min']:5d} {lead}")
    print("-" * len(hdr))
    print(f"WATERSHED: {ws['posture']}   driven by {', '.join(ws['driving_basins']) or '--'}")
    print(f"Warning point {ws['warning_point']}: {ws['warning_point_posture']} "
          f"@ {ws['warning_point_stage_ft']} ft (thresholds {ws['warning_point_thr_ft']})")
    if ws["lead_limited_basins"]:
        print(f"Lead-limited (need forecast-driven warning): "
              f"{', '.join(ws['lead_limited_basins'])}")
    print(ws["qpf_bias_note"])


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        i = sys.argv.index("--demo")
        qpf = float(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 4.8
        w = float(sys.argv[i + 2]) if len(sys.argv) > i + 2 else 0.5
        forcing = {b: {"qpf_in": qpf, "wetness": w} for b in routed_order()}
        fc = forecast_all(forcing)
        fc.update(ok=True, generated_utc="(demo - offline, forced forcing)",
                  source=f"DEMO forcing: QPF={qpf} in, wetness={w}")
        print_report(fc)
    else:
        print_report(run())
