"""
flood_network.py  —  Cullowhee Creek drainage topology + travel-time routing
============================================================================
Wraps flood_engine (point physics at one reach) with the watershed's flow
network. Only sites UPSTREAM of a warning point contribute to it; downstream
sites are excluded automatically by the topology.

Confirmed topology (headwaters meet at Speedwell):
  Double Springs ─┐
                  ├─► Speedwell ─► WCU Campus (warning point)
  AAHP ridge ─────┘                      │
                                         └─► Body Farm  (BELOW campus → excluded)

Lead time to the campus is the SUM of a site's segments along the path
(e.g. Double Springs → Speedwell → Campus), not a single hop.
============================================================================
"""

from dataclasses import dataclass, field

import flood_engine as fe

# ---------------------------------------------------------------------
# TOPOLOGY
#   downstream         : the node this one drains into (None = outlet/off-network)
#   travel_hr_to_down  : travel time of THIS site's single segment to its
#                        immediate downstream node (set by recompute_travel_times)
# ---------------------------------------------------------------------
SITES = {
    "belk": dict(
        name="WCU Campus (Camp Building)", role="warning", elevation_ft=2100.0,
        area_sqmi=9.7, downstream=None, travel_hr_to_down=0.0,
        rain_coll=None, soil_coll=None, stage_coll="noah_campus_stage"),
    "speedwell": dict(
        name="Speedwell", role="confluence", elevation_ft=2050.0, area_sqmi=None,
        downstream="belk", travel_hr_to_down=0.55,       # [SET via reach length]
        rain_coll=None, soil_coll=None, stage_coll=None),
    "double_springs": dict(
        name="Double Springs", role="upstream", elevation_ft=2150.0, area_sqmi=3.2,
        downstream="speedwell", travel_hr_to_down=0.75,  # [SET]
        rain_coll=None, soil_coll=None, stage_coll=None),
    "aahp": dict(
        name="AAHP ridge", role="upstream", elevation_ft=3050.0, area_sqmi=2.1,
        downstream="speedwell", travel_hr_to_down=0.65,  # [SET]
        rain_coll=None, soil_coll=None, stage_coll=None),
    "body_farm": dict(
        name="Body Farm", role="downstream", elevation_ft=2050.0, area_sqmi=None,
        downstream=None, travel_hr_to_down=0.0,          # enters below campus
        rain_coll=None, soil_coll=None, stage_coll=None),
}


def path_travel_hr(site_id, warning_id):
    """Total travel time from a site DOWN to the warning point (sum of segments)."""
    total, cur, seen = 0.0, site_id, set()
    while cur is not None and cur != warning_id and cur not in seen:
        seen.add(cur)
        total += SITES[cur].get("travel_hr_to_down", 0.0) or 0.0
        cur = SITES[cur]["downstream"]
    return round(total, 3) if cur == warning_id else None


def contributing_sites(warning_id):
    """Site ids whose flow passes THROUGH the warning point (upstream of it)."""
    out = []
    for sid, s in SITES.items():
        if sid == warning_id:
            continue
        cur, seen = s["downstream"], set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            if cur == warning_id:
                out.append(sid)
                break
            cur = SITES[cur]["downstream"]
    # nearest (least total travel) first
    return sorted(out, key=lambda x: (path_travel_hr(x, warning_id) or 0.0))


# ---------------------------------------------------------------------
# PER-SITE SIGNALS
# ---------------------------------------------------------------------
def assess_site(site_id, inputs):
    series = (inputs or {}).get("stage_series")
    if not series:
        return None
    return fe.assess(series,
                     soil_moisture_pct=(inputs or {}).get("soil_pct"),
                     storm_rain_in=(inputs or {}).get("storm_rain_in"))


def priming_index(inputs):
    inputs = inputs or {}
    soil = inputs.get("soil_pct")
    rain = inputs.get("storm_rain_in")
    if soil is None and rain is None:
        return None
    cn = fe.dynamic_cn(soil) if soil is not None else fe.CN_NORMAL
    runoff = fe.runoff_depth_in(rain or 0.0, cn)
    s_soil = (soil or 0.0) / 100.0
    s_run = min(1.0, runoff / 1.0)
    return round(0.5 * s_soil + 0.5 * s_run, 3)


def _noisy_or(probs):
    acc = 1.0
    for p in probs:
        acc *= (1.0 - max(0.0, min(1.0, p)))
    return round(1.0 - acc, 3)


# ---------------------------------------------------------------------
# ROUTED WARNING
# ---------------------------------------------------------------------
@dataclass
class UpstreamContribution:
    site_id: str
    name: str
    eta_hr: float
    level: str = None
    ew_prob: float = None
    priming: float = None
    olp_index: float = None


@dataclass
class RoutedWarning:
    warning_site: str
    local: object = None
    upstream: list = field(default_factory=list)
    combined_probability: float = 0.0
    lead_time_hr: float = None
    note: str = ""


def routed_assessment(warning_id, inputs_by_site, prev_level="NORMAL",
                      orographic_by_site=None):
    inputs_by_site = inputs_by_site or {}
    orographic_by_site = orographic_by_site or {}
    local = assess_site(warning_id, inputs_by_site.get(warning_id))

    probs = []
    if local is not None:
        probs.append(local.ew_probability)

    ups, etas, oro_flag = [], [], False
    for sid in contributing_sites(warning_id):
        s = SITES[sid]
        inp = inputs_by_site.get(sid, {})
        eta
