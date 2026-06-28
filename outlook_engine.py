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

import test_model as tm
from flood_rating import posture as _posture   # noqa: F401  (kept for callers)

# Gateway/sensor site -> CC-* basin whose calibration + rating to apply.
SITE_TO_BASIN = {
    "belk": "CC-WCU-2260",
    # "double_springs": "CC-????",   # PENDING: which CC-* basin feeds this gauge
    # "aahp":           "CC-????",   # PENDING
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
    _, res = tm.run_case(qpf_24h_in, p5_in)
    r = res[bid]
    stage = None if r["stage"] is None else round(r["stage"], 1)
    return {"basin": bid,
            "model_q": round(r["qp"]),
            "calib_q": round(r["calib_q"]),
            "forecast_stage_ft": stage,
            "forecast_posture": r["posture"],     # uncapped (context only)
            "outlook_level": _cap(r["posture"])}  # capped at WATCH for the tier


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
