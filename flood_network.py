"""
flood_network.py  —  Cullowhee Creek drainage topology + travel-time routing
============================================================================
Wraps flood_engine (point physics at one reach) with the watershed's flow
network. Only sites UPSTREAM of a warning point contribute to it; downstream
sites are excluded automatically by the topology.

Confirmed topology:
  Double Springs ─┐
  AAHP ridge ─────┤── (travel-time lag) ──► BELK (warning point / outlet)
                                              │
                                              └──► Body Farm  (BELOW Belk → excluded)

This module is pure/testable: it takes a dict of per-site inputs (the Streamlit
app builds that from Firestore) and returns a routed warning. Run directly to
see the synthetic upstream-lead-time demo.
============================================================================
"""

from dataclasses import dataclass, field

import flood_engine as fe

# ---------------------------------------------------------------------
# TOPOLOGY  —  who drains into whom, and the travel time to get there
#   travel_hr_to_down: stream length / flood velocity. [SET from your HDc
#   rating velocity once the reach lengths are pulled off the DEM.]
# ---------------------------------------------------------------------
SITES = {
    "belk": dict(
        name="Belk", role="warning", elevation_ft=2100.0, area_sqmi=9.7,
        downstream=None, travel_hr_to_down=0.0,
        rain_coll=None, soil_coll=None, stage_coll="noah_belk_stage"),
    "double_springs": dict(
        name="Double Springs", role="upstream", elevation_ft=2150.0, area_sqmi=3.2,
        downstream="belk", travel_hr_to_down=3.0,        # [SET]
        rain_coll=None, soil_coll=None, stage_coll=None),
    "aahp": dict(
        name="AAHP ridge", role="upstream", elevation_ft=3050.0, area_sqmi=2.1,
        downstream="belk", travel_hr_to_down=2.0,        # [SET]
        rain_coll=None, soil_coll=None, stage_coll=None),
    "body_farm": dict(
        name="Body Farm", role="downstream", elevation_ft=2050.0, area_sqmi=None,
        downstream=None, travel_hr_to_down=0.0,          # enters below Belk
        rain_coll=None, soil_coll=None, stage_coll=None),
}


def contributing_sites(warning_id):
    """Site ids whose flow passes THROUGH the warning point (i.e. upstream of it)."""
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
    # order by travel time so the nearest (least lead time) is first
    return sorted(out, key=lambda x: SITES[x]["travel_hr_to_down"])


# ---------------------------------------------------------------------
# PER-SITE SIGNALS
# ---------------------------------------------------------------------
def assess_site(site_id, inputs):
    """Full flood_engine assessment IF this site has a stage series; else None."""
    series = (inputs or {}).get("stage_series")
    if not series:
        return None
    return fe.assess(series,
                     soil_moisture_pct=(inputs or {}).get("soil_pct"),
                     storm_rain_in=(inputs or {}).get("storm_rain_in"))


def priming_index(inputs):
    """
    Leading-indicator [0,1] for a site with no stage sensor yet: how 'primed'
    its sub-basin is, from soil saturation + TR-55 runoff of recent rain.
    """
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
    """Combine independent warning signals: 1 - Π(1 - p)."""
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
    olp_index: float = None          # orographic lift potential (pre-rain)


@dataclass
class RoutedWarning:
    warning_site: str
    local: object = None              # flood_engine FloodAssessment, or None
    upstream: list = field(default_factory=list)
    combined_probability: float = 0.0
    lead_time_hr: float = None        # earliest upstream arrival that's alerting
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
        a = assess_site(sid, inp)
        c = UpstreamContribution(site_id=sid, name=s["name"],
                                 eta_hr=s["travel_hr_to_down"])
        if a is not None:                       # upstream has live stage
            c.level, c.ew_prob = a.level, a.ew_probability
            probs.append(a.ew_probability)
            if a.level != "NORMAL":
                etas.append(s["travel_hr_to_down"])
        else:                                   # only rain/soil priming
            prim = priming_index(inp)
            if prim is not None:
                c.priming = prim
                probs.append(prim)
                if prim >= 0.5:
                    etas.append(s["travel_hr_to_down"])
        # orographic lift = PRE-RAIN signal: enhanced-rain risk on this sub-basin
        olp = orographic_by_site.get(sid)
        if olp is not None:
            c.olp_index = round(olp, 3)
            # weight it modestly — it's a precursor, not yet runoff
            probs.append(0.6 * olp)
            if olp >= 0.5:
                oro_flag = True
        ups.append(c)

    combined = _noisy_or(probs) if probs else 0.0
    lead = min(etas) if etas else None

    if local is None and not any(p for p in probs):
        note = "No live inputs anywhere yet — engine idle."
    elif local is None:
        note = ("Belk has no stage sensor yet; the only flood signal is from "
                "UPSTREAM sites. This is warning Belk before its own gauge exists.")
    elif lead is not None:
        note = f"Upstream alert in the system — ~{lead} hr of lead time to Belk."
    else:
        note = "Local stage only; no upstream alert."
    if oro_flag:
        note += "  Orographic lift elevated upslope — enhanced-rain risk BEFORE rain falls."

    return RoutedWarning(warning_id, local, ups, combined, lead, note)


# =====================================================================
# SELF-TEST  —  synthetic, no live sensors
# =====================================================================
def _flat(stage, hours=2, dt_min=5):
    pts, t = [], 0
    for _ in range((hours * 60) // dt_min):
        pts.append((t * 60, stage)); t += dt_min
    return pts


def _rising(start, end, hours=2, dt_min=5):
    pts, t = [], 0
    n = (hours * 60) // dt_min
    for k in range(n):
        frac = (k + 1) / n
        pts.append((t * 60, round(start + (end - start) * (frac ** 1.6), 3)))
        t += dt_min
    return pts


def _show(title, rw):
    print("=" * 68)
    print(title)
    print("=" * 68)
    if rw.local:
        print(f"  Belk local stage : {rw.local.stage_ft} ft  ->  {rw.local.level} "
              f"(P={rw.local.ew_probability})")
    else:
        print("  Belk local stage : (no stage sensor online)")
    for c in rw.upstream:
        if c.level is not None:
            print(f"  upstream {c.name:<15} {c.level:<9} P={c.ew_prob}  "
                  f"arrives in ~{c.eta_hr} hr")
        elif c.priming is not None:
            print(f"  upstream {c.name:<15} priming={c.priming}  arrives in ~{c.eta_hr} hr")
        else:
            print(f"  upstream {c.name:<15} (no inputs online)")
    print(f"  COMBINED warning probability : {rw.combined_probability}")
    print(f"  Lead time available          : "
          f"{rw.lead_time_hr if rw.lead_time_hr is not None else '--'} hr")
    print(f"  note: {rw.note}\n")


def _run_self_test():
    print("Contributing to Belk (upstream only):", contributing_sites("belk"))
    print("  -> Body Farm correctly excluded (drains below Belk)\n")

    # Scenario A: Belk has NO stage sensor, but Double Springs (upstream) is rising.
    inputs_a = {
        "double_springs": {"stage_series": _rising(4.0, 9.5)},   # upstream surging
        "aahp": {"soil_pct": 88.0, "storm_rain_in": 1.8},        # primed, rain-only
    }
    _show("SCENARIO A — upstream pulse, Belk gauge not yet installed",
          routed_assessment("belk", inputs_a))

    # Scenario B: everything quiet.
    inputs_b = {
        "belk": {"stage_series": _flat(4.0)},
        "double_springs": {"stage_series": _flat(3.5)},
        "aahp": {"soil_pct": 30.0, "storm_rain_in": 0.0},
    }
    _show("SCENARIO B — calm baseflow everywhere",
          routed_assessment("belk", inputs_b))


if __name__ == "__main__":
    _run_self_test()


# =====================================================================
# TRAVEL-TIME ESTIMATION  (kinematic-wave celerity from the HDc rating)
# =====================================================================
#   A flood wave translates at the wave celerity, not the mean water
#   velocity. For a wide channel (Manning), celerity c ~= (5/3)*V_mean.
#   V_mean = Q(stage)/A(stage), both from flood_engine at a reference
#   flood stage. travel_time = reach_length / c.
#
#   SET these reach lengths from the DEM / stream network (stream-feet
#   along the channel from each upstream node down to Belk's gauge).
# ---------------------------------------------------------------------
REACH_LENGTH_FT = {
    "double_springs": 42000.0,   # ~8 stream-mi  [EXAMPLE — measure off DEM]
    "aahp":           31000.0,   # ~6 stream-mi  [EXAMPLE — measure off DEM]
}
REF_FLOOD_STAGE_FT = 9.0         # stage at which to evaluate wave speed (WARNING)
CELERITY_BETA      = 5.0 / 3.0   # wide-channel kinematic-wave factor


def mean_velocity_fps(stage_ft):
    area, _ = fe.channel_geometry(stage_ft)
    if area <= 0:
        return 0.0
    return fe.mannings_discharge_cfs(stage_ft) / area


def wave_celerity_fps(stage_ft, beta=CELERITY_BETA):
    return beta * mean_velocity_fps(stage_ft)


def travel_time_hr(reach_length_ft, stage_ft=REF_FLOOD_STAGE_FT, beta=CELERITY_BETA):
    c = wave_celerity_fps(stage_ft, beta)
    if c <= 0:
        return None
    return reach_length_ft / c / 3600.0


def recompute_travel_times(stage_ft=REF_FLOOD_STAGE_FT):
    """Update each upstream site's travel_hr_to_down from its reach length."""
    out = {}
    for sid, length in REACH_LENGTH_FT.items():
        tt = travel_time_hr(length, stage_ft)
        if tt is not None and sid in SITES:
            SITES[sid]["travel_hr_to_down"] = round(tt, 2)
            out[sid] = round(tt, 2)
    return out


# =====================================================================
# FIVE-CLOCK LEAD TIME  (rain -> runoff generation -> channel routing -> Belk)
# =====================================================================
#   total lead time at Belk = hillslope/generation lag (clock 3, soil-dependent)
#                           + channel routing (clock 4, celerity).
#   Clock 1 (rain) is t=0; clock 2 (infiltration) sets runoff MAGNITUDE, not delay.
#   SUBBASIN_TC_HR are placeholders — [SET] from sub-basin time of concentration.
# ---------------------------------------------------------------------
SUBBASIN_TC_HR = {"double_springs": 1.0, "aahp": 0.7}   # [SET]


def hillslope_lag_hr(site_id, soil_pct):
    tc = SUBBASIN_TC_HR.get(site_id)
    if tc is None:
        return None
    soil = 50.0 if soil_pct is None else soil_pct
    wetness = 1.3 - 0.6 * (soil / 100.0)     # dry => slower, saturated => flashier
    return tc * wetness


def lead_time_breakdown(site_id, rain_in, soil_pct=None, ref_stage=REF_FLOOD_STAGE_FT):
    cn = fe.dynamic_cn(soil_pct)
    Q = fe.runoff_depth_in(rain_in, cn)
    hill = hillslope_lag_hr(site_id, soil_pct)
    chan = travel_time_hr(REACH_LENGTH_FT.get(site_id, 0.0), ref_stage)
    total = (hill or 0.0) + (chan or 0.0)
    S = 1000.0 / cn - 10.0
    Ia = 0.2 * S
    iap = Ia / rain_in if rain_in > 0 else 0.5
    area = SITES[site_id].get("area_sqmi") or 0.0
    qu = fe._unit_peak_q(SUBBASIN_TC_HR.get(site_id, 1.0), iap)
    qp = qu * area * Q * fe.POND_FACTOR
    return {
        "site": SITES[site_id]["name"], "rain_in": rain_in, "soil_pct": soil_pct,
        "cn": round(cn, 1), "runoff_in": round(Q, 3),
        "hillslope_hr": round(hill, 2) if hill is not None else None,
        "channel_hr": round(chan, 2) if chan is not None else None,
        "total_lead_hr": round(total, 2), "peak_cfs": round(qp, 0),
    }


# =====================================================================
# PROVENANCE  —  every value is one of: placeholder | modeled | live
#   placeholder : a typed guess; replace ONCE with a survey/calibration
#   modeled     : computed; never sensed directly, but VALIDATED vs sensors
#   live        : set at runtime when a real sensor feeds it (per-site;
#                 the dashboard determines this, not this module)
# ---------------------------------------------------------------------
MODEL_PROVENANCE = {
    "HDc":               ("placeholder", "set to the JAWRA value"),
    "reach_lengths":     ("placeholder", "measure along channel off the DEM"),
    "subbasin_tc":       ("placeholder", "time of concentration per sub-basin"),
    "mannings_n":        ("placeholder", "calibrate the constructed channel"),
    "channel_routing":   ("modeled", "celerity from HDc rating — validate with 2-point stage"),
    "hillslope_lag":     ("modeled", "tc-based — validate against observed event timing"),
    "runoff_partition":  ("modeled", "TR-55 dynamic CN — depends on the soil input"),
    "logistic_weights":  ("placeholder", "calibrate against observed flood events"),
    "orographic_terrain": ("placeholder", "upslope azimuth + slope per windward site from DEM"),
    "orographic_index":   ("modeled", "lift potential from ridge wind + BME280"),
}


def describe_provenance():
    """List of (item, status, note) for display."""
    return [(k, v[0], v[1]) for k, v in MODEL_PROVENANCE.items()]
