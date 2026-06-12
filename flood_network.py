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
        eta = path_travel_hr(sid, warning_id)
        if eta is None:
            eta = s["travel_hr_to_down"]
        a = assess_site(sid, inp)
        c = UpstreamContribution(site_id=sid, name=s["name"], eta_hr=eta)
        if a is not None:
            c.level, c.ew_prob = a.level, a.ew_probability
            probs.append(a.ew_probability)
            if a.level != "NORMAL":
                etas.append(eta)
        else:
            prim = priming_index(inp)
            if prim is not None:
                c.priming = prim
                probs.append(prim)
                if prim >= 0.5:
                    etas.append(eta)
        olp = orographic_by_site.get(sid)
        if olp is not None:
            c.olp_index = round(olp, 3)
            probs.append(0.6 * olp)
            if olp >= 0.5:
                oro_flag = True
        ups.append(c)

    combined = _noisy_or(probs) if probs else 0.0
    lead = min(etas) if etas else None

    if local is None and not any(p for p in probs):
        note = "No live inputs anywhere yet — engine idle."
    elif local is None:
        note = ("Campus has no stage sensor yet; the only flood signal is from "
                "UPSTREAM sites. This is warning the campus before its own gauge exists.")
    elif lead is not None:
        note = f"Upstream alert in the system — ~{lead} hr of lead time to the campus."
    else:
        note = "Local stage only; no upstream alert."
    if oro_flag:
        note += "  Orographic lift elevated upslope — enhanced-rain risk BEFORE rain falls."

    return RoutedWarning(warning_id, local, ups, combined, lead, note)


# =====================================================================
# TWO-TIER POSTURE  (Outlook vs. Confirmation)
# =====================================================================
# The system serves SMALL ungauged headwaters above the gauged mainstem, so
# the only forward signal before the creek responds is soil + forecast rain.
# That evidence is allowed to raise an OUTLOOK to WATCH (lead-time, lower
# confidence, false-alarm prone). WARNING / EMERGENCY require CONFIRMATION
# from a measured headwater stage rise. The gauged downstream mainstem is a
# validation reference and is intentionally NOT an input to either tier.
WATCH_OUTLOOK_THRESHOLD = 0.45   # relative priming index that trips an Outlook [tunable]


@dataclass
class TieredPosture:
    headline: str = "NORMAL"
    driver: str = "none"               # "outlook" | "stream" | "none"
    outlook_level: str = "NORMAL"      # NORMAL | WATCH  (capped at WATCH)
    outlook_risk: float = 0.0          # RELATIVE index 0..1 (uncalibrated)
    outlook_sites: list = field(default_factory=list)   # (name, value, eta_hr, kind)
    outlook_note: str = ""
    stream_level: str = "NORMAL"       # NORMAL..EMERGENCY (measured stage only)
    stream_confirmed: bool = False
    stream_sites: list = field(default_factory=list)    # (name, level, stage_ft, eta_hr)
    stream_note: str = ""
    lead_hr: float = None
    headline_statement: str = ""


def _sev_idx(level):
    order = ["NORMAL", "WATCH", "WARNING", "EMERGENCY"]
    return order.index(level) if level in order else 0


def tiered_posture(rw, warning_id="belk"):
    """Split a RoutedWarning into the Outlook (forecast/soil) and Confirmation
    (measured stream) tiers, enforcing: forecast/soil can reach at most WATCH;
    WARNING/EMERGENCY require a measured stream rise."""
    tp = TieredPosture(lead_hr=rw.lead_time_hr)

    # ---- Confirmation tier: measured stage only --------------------------
    stream_levels = []
    if rw.local is not None:
        tp.stream_confirmed = True
        stream_levels.append(rw.local.level)
        tp.stream_sites.append((SITES[warning_id]["name"], rw.local.level,
                                getattr(rw.local, "stage_ft", None), 0.0))
    for c in rw.upstream:
        if c.level is not None:                      # has measured stage
            tp.stream_confirmed = True
            stream_levels.append(c.level)
            tp.stream_sites.append((c.name, c.level, None, c.eta_hr))
    tp.stream_level = max(stream_levels, key=_sev_idx) if stream_levels else "NORMAL"

    # ---- Outlook tier: soil + forecast (+ pre-rain orographic) -----------
    primings, rising_names = [], []
    for c in rw.upstream:
        if c.priming is not None:
            primings.append(c.priming)
            tp.outlook_sites.append((c.name, c.priming, c.eta_hr, "soil+rain"))
            if c.priming >= WATCH_OUTLOOK_THRESHOLD:
                rising_names.append(c.name)
        if c.olp_index is not None:
            primings.append(0.6 * c.olp_index)
            tp.outlook_sites.append((c.name, c.olp_index, c.eta_hr, "orographic"))
    tp.outlook_risk = _noisy_or(primings) if primings else 0.0
    tp.outlook_level = "WATCH" if tp.outlook_risk >= WATCH_OUTLOOK_THRESHOLD else "NORMAL"

    # ---- Combine: outlook capped at WATCH; stream sets the ceiling -------
    tp.headline = max([tp.outlook_level, tp.stream_level], key=_sev_idx)
    if tp.stream_level != "NORMAL" and _sev_idx(tp.stream_level) >= _sev_idx(tp.outlook_level):
        tp.driver = "stream"
    elif tp.outlook_level == "WATCH":
        tp.driver = "outlook"
    else:
        tp.driver = "none"

    # ---- Tier notes ------------------------------------------------------
    if tp.outlook_level == "WATCH":
        who = ", ".join(dict.fromkeys(rising_names)) or "upstream sub-basins"
        lead = f" ~{tp.lead_hr:.1f} hr before the creek responds" if tp.lead_hr else ""
        tp.outlook_note = (f"Saturated soils + forecast rain have primed {who}{lead}. "
                           "Forecast-based and uncalibrated — treat as relative risk, not a "
                           "calibrated probability.")
    else:
        tp.outlook_note = "Soil + forecast signal below outlook threshold."

    if tp.stream_confirmed and tp.stream_level != "NORMAL":
        names = ", ".join(n for n, lv, *_ in tp.stream_sites if lv != "NORMAL")
        tp.stream_note = f"Measured stage rising at {names} — confirmed."
    elif tp.stream_confirmed:
        tp.stream_note = "Stage sensors online; no rise measured yet."
    else:
        tp.stream_note = ("No headwater stage sensor reporting yet — confirmation tier "
                          "pending deployment. This is the data only NOAH provides.")

    # ---- Headline statement by driver -----------------------------------
    if tp.driver == "stream":
        tp.headline_statement = f"Measured headwater stream rise — {tp.stream_level} confirmed."
    elif tp.driver == "outlook":
        tp.headline_statement = ("OUTLOOK: sub-basins primed by soil + forecast. Lead-time signal, "
                                 "not yet confirmed by stream rise.")
    else:
        tp.headline_statement = "No flood threat indicated. Monitoring nominal."
    return tp


# =====================================================================
# SELF-TEST
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
        pts.append((t * 60, round(start + (end - start) * (frac ** 1.6), 3))); t += dt_min
    return pts


def _show(title, rw):
    print("=" * 70); print(title); print("=" * 70)
    if rw.local:
        print(f"  Campus stage : {rw.local.stage_ft} ft -> {rw.local.level} (P={rw.local.ew_probability})")
    else:
        print("  Campus stage : (no gauge online)")
    for c in rw.upstream:
        tag = (f"{c.level} P={c.ew_prob}" if c.level else
               f"priming={c.priming}" if c.priming is not None else "(no inputs)")
        print(f"  {c.name:<22} {tag:<22} reaches campus in ~{c.eta_hr} hr")
    print(f"  COMBINED probability : {rw.combined_probability}")
    print(f"  Lead time to campus  : {rw.lead_time_hr if rw.lead_time_hr is not None else '--'} hr")
    print(f"  note: {rw.note}\n")


def _run_self_test():
    recompute_travel_times()
    print("Contributing (sorted by total travel):", contributing_sites("belk"))
    print("  -> Body Farm excluded (below campus); Speedwell included as confluence\n")
    inp = {"double_springs": {"stage_series": _rising(4.0, 9.5)},
           "aahp": {"soil_pct": 88.0, "storm_rain_in": 1.8}}
    _show("Upstream pulse, campus gauge not yet installed", routed_assessment("belk", inp))


# =====================================================================
# TRAVEL TIME  (kinematic-wave celerity per segment)
# =====================================================================
REACH_LENGTH_FT = {
    "double_springs": 30000.0,   # DS -> Speedwell        ~5.7 mi  [EXAMPLE — measure off DEM]
    "aahp":           26000.0,   # AAHP -> Speedwell      ~4.9 mi  [EXAMPLE]
    "speedwell":      22000.0,   # Speedwell -> Campus    ~4.2 mi  [EXAMPLE — Tuckasegee mainstem]
}
# Optional per-segment celerity override (ft/s). The Speedwell -> Campus reach
# runs down the larger Tuckasegee mainstem and likely moves faster than the
# headwater channels — set its celerity here once known. [SET]
SEGMENT_CELERITY_FPS = {
    # "speedwell": 14.0,
}
REF_FLOOD_STAGE_FT = 9.0
CELERITY_BETA = 5.0 / 3.0


def mean_velocity_fps(stage_ft):
    area, _ = fe.channel_geometry(stage_ft)
    if area <= 0:
        return 0.0
    return fe.mannings_discharge_cfs(stage_ft) / area


def wave_celerity_fps(stage_ft, beta=CELERITY_BETA):
    return beta * mean_velocity_fps(stage_ft)


def travel_time_hr(reach_length_ft, stage_ft=REF_FLOOD_STAGE_FT, beta=CELERITY_BETA):
    c = wave_celerity_fps(stage_ft, beta)
    return reach_length_ft / c / 3600.0 if c > 0 else None


def recompute_travel_times(stage_ft=REF_FLOOD_STAGE_FT):
    """Set each segment's travel_hr_to_down from its reach length (or celerity override)."""
    out = {}
    for sid, length in REACH_LENGTH_FT.items():
        if sid in SEGMENT_CELERITY_FPS:
            c = SEGMENT_CELERITY_FPS[sid]
            tt = length / c / 3600.0 if c > 0 else None
        else:
            tt = travel_time_hr(length, stage_ft)
        if tt is not None and sid in SITES:
            SITES[sid]["travel_hr_to_down"] = round(tt, 2)
            out[sid] = round(tt, 2)
    return out


# =====================================================================
# FIVE-CLOCK LEAD TIME
# =====================================================================
SUBBASIN_TC_HR = {"double_springs": 1.0, "aahp": 0.7}   # [SET]


def hillslope_lag_hr(site_id, soil_pct):
    tc = SUBBASIN_TC_HR.get(site_id)
    if tc is None:
        return None
    soil = 50.0 if soil_pct is None else soil_pct
    return tc * (1.3 - 0.6 * (soil / 100.0))


def lead_time_breakdown(site_id, rain_in, soil_pct=None, warning_id="belk"):
    cn = fe.dynamic_cn(soil_pct)
    Q = fe.runoff_depth_in(rain_in, cn)
    hill = hillslope_lag_hr(site_id, soil_pct)
    chan = path_travel_hr(site_id, warning_id)     # full path to the campus
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
# PROVENANCE
# =====================================================================
MODEL_PROVENANCE = {
    "HDc":               ("placeholder", "set to the JAWRA value"),
    "reach_lengths":     ("placeholder", "measure each segment along channel off the DEM"),
    "subbasin_tc":       ("placeholder", "time of concentration per sub-basin"),
    "mannings_n":        ("placeholder", "calibrate the constructed channel"),
    "channel_routing":   ("modeled", "celerity from HDc rating — validate with multi-point stage"),
    "hillslope_lag":     ("modeled", "tc-based — validate against observed event timing"),
    "runoff_partition":  ("modeled", "TR-55 dynamic CN — depends on the soil input"),
    "logistic_weights":  ("placeholder", "calibrate against observed flood events"),
    "orographic_terrain": ("placeholder", "upslope azimuth + slope per windward site from DEM"),
    "orographic_index":   ("modeled", "lift potential from ridge wind + BME280"),
}


def describe_provenance():
    return [(k, v[0], v[1]) for k, v in MODEL_PROVENANCE.items()]


if __name__ == "__main__":
    _run_self_test()
