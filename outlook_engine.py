"""
outlook_engine.py - bridge between the corrected engine and flood_network's
Outlook tier.

Replaces flood_network.priming_index (a relative 0-1 index: 0.5*soil +
0.5*runoff) with a CALIBRATED forecast stage + posture from QPF, run through
basins.py + flood_rating.py, for any gateway site that maps to a CC-* basin.

Respects flood_network's two-tier rule: the Outlook is capped at WATCH. Only a
measured stage rise (Confirmation tier) may reach WARNING / EMERGENCY.

MAPPING STATUS
  belk -> CC-WCU-2260 is the one confirmed gateway<->basin mapping (both are the
  campus warning point). The headwater SENSOR sites (double_springs, aahp) are
  not CC-* delineations; fill SITE_TO_BASIN once you decide which basin each
  gauge draws from. Until then forecast_site() returns None for them and the
  caller keeps priming_index.

HOOK (in flood_network.tiered_posture, Outlook tier):
    from outlook_engine import forecast_site
    fc = forecast_site(c.site_id, qpf_24h_in, p5_in)
    if fc is not None:
        # use fc["outlook_level"] (capped WATCH) and fc["forecast_stage_ft"]
        # instead of the relative priming index for this site
    else:
        ... existing priming_index path ...
"""

# Sourced from wetness.py + flood_rating, NOT test_model. test_model moved to
# the private Cullowhee-Engine repo in 9c720eb, which left this module raising
# ModuleNotFoundError on import; and flood_rating has never exported a bare
# `posture` - the name is `posture_stage`. Both were latent: nothing on main
# imports outlook_engine yet, so the breakage only showed on a direct import.
from wetness import assess_wet, resolve_wetness
from flood_rating import posture_stage as _posture   # noqa: F401 (kept for callers)

# Gateway/sensor site -> CC-* basin whose calibration + rating to apply.
SITE_TO_BASIN = {
    "belk":           "CC-WCU-2260",
    "double_springs": "CC-MS-1100",   # PIP: 34 m off the upper-mainstem divide -> MS boundary node
    "aahp":           "CC-TIL-705",   # PIP: 544 m inside the Tilley Creek sub-basin
    # speedwell intentionally unmapped: it nests MS + TIL, so mapping it would double-count
}

_ORDER = ["NORMAL", "WATCH", "WARNING", "EMERGENCY"]
_OUTLOOK_CAP = "WATCH"

def _cap(level):
    """Outlook ceiling: forecast evidence may not exceed WATCH (flood_network rule)."""
    return level if _ORDER.index(level) <= _ORDER.index(_OUTLOOK_CAP) else _OUTLOOK_CAP


def forecast_basin(bid, qpf_24h_in, p5_in):
    """Engine forecast for one CC-* basin from a 24-hr QPF total + 5-day antecedent.

    Returns calibrated peak, forecast stage (engine rating), the raw forecast
    posture, and the WATCH-capped level for Outlook use.
    """
    # wetness.assess_wet is the continuous-CN successor to test_model.run_case's
    # per-basin body: same NRCS chain, but a decayed 30-day API instead of the
    # 5-day ARC staircase, and a baseflow-inclusive total stage. p5_in is
    # carried on the legacy 5-day scale, so convert it through the same source
    # ladder live.html and wetness.py use rather than re-deriving it here.
    w, _src = resolve_wetness(p5_in=p5_in)
    r = assess_wet(bid, qpf_24h_in, w)
    stage = None if r["stage_ft"] is None else round(r["stage_ft"], 1)
    return {"basin": bid,
            "model_q": round(r["qp"]),
            "calib_q": round(r["calib_q"]),
            "wetness": round(w, 3),
            "forecast_stage_ft": stage,
            "forecast_posture": r["posture"],     # uncapped (context only)
            "outlook_level": _cap(r["posture"])}  # capped at WATCH for the tier


def forecast_basin_ens(bid, member_qpf24_in, wetness=None, wetness_src=None,
                       soil_pct=None, api_in=None, p5_in=None):
    """ENSEMBLE forecast_basin: one engine run per WeatherNext member instead
    of one run per QPF. Wetness is resolved ONCE through the standard source
    ladder (soil percentile > API30 > legacy p5) and shared by all members —
    the members carry the WEATHER spread; the soil state is a measurement of
    now, not a forecast. (flood_ensemble.ensemble_members adds the wetness
    perturbation when you want both axes; this function is the feed path.)

    member_qpf24_in: per-member 24-h QPF (inches), ALREADY bias-corrected
    (weathernext_source.apply_bias). Use max_window_totals() — the worst 24 h
    each member produces in the horizon — not the first 24 h of lead time.

    Returns stage quantiles + exceedance probabilities. `outlook_level` is
    the WATCH-capped modal posture (two-tier rule unchanged: forecast
    evidence, however probable, cannot assert a flood is happening)."""
    from weathernext_source import quantiles
    if not member_qpf24_in:
        raise ValueError("no ensemble members — use forecast_basin()")
    if wetness is None:
        wetness, wetness_src = resolve_wetness(
            soil_pct=soil_pct, api_in=api_in, p5_in=p5_in)
    stages, postures = [], []
    for q in member_qpf24_in:
        r = assess_wet(bid, q, wetness)
        if r["stage_ft"] is not None:
            stages.append(round(r["stage_ft"], 2))
        postures.append(r["posture"])
    n = len(postures)
    rank = {k: i for i, k in enumerate(_ORDER)}
    p_exceed = {lvl: round(sum(1 for p in postures
                               if p in rank and rank[p] >= rank[lvl]) / n, 3)
                for lvl in ("WATCH", "WARNING", "EMERGENCY")}
    counts = {}
    for p in postures:
        counts[p] = counts.get(p, 0) + 1
    modal = max(counts, key=lambda k: (counts[k], -rank.get(k, 99)))
    return {"basin": bid, "n_members": n,
            "wetness": round(wetness, 3), "wetness_src": wetness_src,
            "qpf24_in": quantiles(member_qpf24_in),
            "stage_ft": quantiles(stages) if stages else None,
            "p_exceed": p_exceed,
            "forecast_posture": modal,          # uncapped (context only)
            "outlook_level": _cap(modal)}       # capped at WATCH for the tier


def forecast_site(site_id, qpf_24h_in, p5_in):
    """forecast_basin keyed by a flood_network gateway site.
    Returns None if the site has no basin mapping yet (caller keeps priming_index)."""
    bid = SITE_TO_BASIN.get(site_id)
    if bid is None:
        return None
    out = forecast_basin(bid, qpf_24h_in, p5_in)
    out["site_id"] = site_id
    return out


def campus_outlook(qpf_24h_in, p5_in):
    """Convenience: engine forecast for the campus warning point (belk)."""
    return forecast_site("belk", qpf_24h_in, p5_in)


if __name__ == "__main__":
    print("Campus Outlook forecast (engine) vs flood_network's relative priming index:")
    print(f"  {'QPF/24h':>8}{'antecedent':>12}{'calib Q':>9}{'fcst stage':>11}"
          f"{'fcst':>10}{'outlook':>9}")
    for depth, lbl in [(3.2, "2-yr"), (4.8, "10-yr"), (6.6, "50-yr"), (7.5, "100-yr")]:
        for p5, an in [(0.2, "dry"), (1.7, "normal"), (3.0, "wet")]:
            fc = campus_outlook(depth, p5)
            print(f"  {depth:>6}\" {an:>11}{fc['calib_q']:>9,}"
                  f"{fc['forecast_stage_ft']:>9} ft{fc['forecast_posture']:>10}"
                  f"{fc['outlook_level']:>9}")
    print("\nForecast posture is engine-calibrated; outlook column is capped at WATCH")
    print("per flood_network (only measured stage confirms WARNING/EMERGENCY).")

    print("\nENSEMBLE PATH (forecast_basin_ens, WeatherNext fixture members):")
    import weathernext_source as wn
    d = wn.make_fixture()
    for bid in ("CC-WCU-2260", "CC-UP-503"):
        m24, _ = wn.max_window_totals(d["basins"][bid], 24, horizon_hr=72)
        m24 = wn.apply_bias(m24, wn.bias_mult(bid))
        fc = forecast_basin_ens(bid, m24, p5_in=1.7)
        pe, s = fc["p_exceed"], fc["stage_ft"]
        assert pe["WATCH"] >= pe["WARNING"] >= pe["EMERGENCY"]
        assert _ORDER.index(fc["outlook_level"]) <= _ORDER.index(_OUTLOOK_CAP)
        print(f"  {bid:14s} n={fc['n_members']}  qpf24 p50={fc['qpf24_in']['p50']}\""
              f"  stage p10/50/90={s['p10']}/{s['p50']}/{s['p90']} ft"
              f"  P(W/Wr/E)={pe['WATCH']:.0%}/{pe['WARNING']:.0%}/{pe['EMERGENCY']:.0%}"
              f"  outlook={fc['outlook_level']}")
    print("  ensemble self-checks passed (exceedance monotone, WATCH cap held)")
