"""
flood_engine.py  —  Cullowhee Creek flood decision engine
==========================================================
Pure-computation flood logic for the NOAH / Argonaut-SW stage node.
Input-agnostic: it takes a stage time series (and optional rainfall /
soil context) and returns a decision. It does NOT read Firestore —
that wiring lives in the Cloud Function wrapper, so this module can be
unit-tested today against synthetic stage data, with zero live sensors.

Implements:
  1. HDc-corrected Manning's discharge  (n_eff = n_nom / HDc)
  2. Hysteresis-stabilized stage classification at 7 / 9 / 11 ft
  3. Least-squares rate-of-rise + time-to-threshold projection
  4. TR-55 Type II graphical peak discharge w/ dynamic CN
  5. Weighted logistic early-warning probability

Run directly ( python flood_engine.py ) to execute the synthetic
hydrograph self-test.
==========================================================
"""

import math
from dataclasses import dataclass, field

# =====================================================================
# CONFIG  —  values marked [CONFIRM] come from your manuscript / survey
# =====================================================================

# --- Channel geometry: 12-ft constructed Cullowhee Creek channel ---
CHANNEL_WIDTH_FT   = 12.0     # bottom width
CHANNEL_SIDE_SLOPE = 0.0      # z (horizontal:vertical); 0 = rectangular [CONFIRM]
BED_SLOPE          = 0.0045   # ft/ft, from TVA 1983 cross-sections (~0.004–0.005)
MANNINGS_N_NOM     = 0.035    # nominal n for the constructed channel  [CONFIRM]
HDC                = 1.00     # HDc correction factor from JAWRA paper [CONFIRM — set real value]
#   n_eff = MANNINGS_N_NOM / HDC   (HDc>1 raises discharge for a given stage)

BASEFLOW_FT        = 4.0      # observed clear-day baseflow depth

# --- Stage thresholds (ft) and hysteresis ---
THRESH = {"WATCH": 7.0, "WARNING": 9.0, "EMERGENCY": 11.0}
LEVELS = ["NORMAL", "WATCH", "WARNING", "EMERGENCY"]
DEADBAND_FT = 0.5            # must fall this far below a level's entry to de-escalate

# --- Rate-of-rise ---
RATE_WINDOW_MIN = 30        # minutes of history used for the least-squares slope
RATE_REF_FT_HR  = 1.0       # normalizing reference rise rate for the logistic

# --- Watershed / TR-55 ---
DRAINAGE_AREA_SQMI = 9.7    # Cullowhee Creek watershed
TIME_OF_CONC_HR    = 1.5    # time of concentration  [CONFIRM]
CN_NORMAL          = 74.0   # AMC-II curve number for the watershed [CONFIRM]
POND_FACTOR        = 1.0    # TR-55 F_p (1.0 = no ponding/swamp)

# --- Logistic early-warning weights (calibrate against events) [CONFIRM] ---
LOGIT = {"bias": -4.0, "stage": 5.0, "rate": 2.5, "soil": 1.5, "rain": 2.0}
RAIN_REF_IN = 2.0           # normalizing reference storm depth

# TR-55 Type II unit-peak-discharge coefficients (log10 q_u vs log10 tc),
# indexed by Ia/P.  Source: TR-55 (1986) Exhibit 4-II / Appendix F.
_TYPE_II = {
    0.10: (2.55323, -0.61512, -0.16403),
    0.30: (2.46532, -0.62257, -0.11657),
    0.35: (2.41896, -0.61594, -0.08820),
    0.40: (2.36409, -0.59857, -0.05621),
    0.45: (2.29238, -0.57005, -0.02281),
    0.50: (2.20282, -0.51599, -0.01259),
}


# =====================================================================
# 1. HDc-CORRECTED MANNING'S DISCHARGE
# =====================================================================
def channel_geometry(depth_ft):
    """Area and wetted perimeter for a (rectangular or trapezoidal) channel."""
    b, z = CHANNEL_WIDTH_FT, CHANNEL_SIDE_SLOPE
    y = max(0.0, depth_ft)
    area = (b + z * y) * y
    perim = b + 2.0 * y * math.sqrt(1.0 + z * z)
    return area, perim


def mannings_discharge_cfs(depth_ft):
    """
    HDc-corrected Manning's discharge (US units, k=1.49).
    n_eff = n_nom / HDc, so Q = (1.49 * HDc / n_nom) * A * R^(2/3) * S^(1/2).
    """
    area, perim = channel_geometry(depth_ft)
    if area <= 0 or perim <= 0:
        return 0.0
    R = area / perim
    n_eff = MANNINGS_N_NOM / HDC
    return (1.49 / n_eff) * area * (R ** (2.0 / 3.0)) * math.sqrt(BED_SLOPE)


# =====================================================================
# 2. STAGE CLASSIFICATION WITH HYSTERESIS
# =====================================================================
def classify_stage(stage_ft, prev_level="NORMAL"):
    """
    Map a stage to NORMAL/WATCH/WARNING/EMERGENCY with hysteresis so the
    state doesn't flap around a threshold. Escalation is immediate on
    crossing up; de-escalation requires falling DEADBAND_FT below the
    current level's entry threshold.
    """
    entry = [0.0, THRESH["WATCH"], THRESH["WARNING"], THRESH["EMERGENCY"]]
    cur = LEVELS.index(prev_level) if prev_level in LEVELS else 0

    # escalate to the highest threshold currently exceeded
    target = 0
    for i, t in enumerate(entry):
        if stage_ft >= t:
            target = i
    if target > cur:
        return LEVELS[target]

    # de-escalate only past the deadband
    while cur > 0 and stage_ft < entry[cur] - DEADBAND_FT:
        cur -= 1
    return LEVELS[cur]


# =====================================================================
# 3. RATE OF RISE  (least squares)  +  TIME-TO-THRESHOLD
# =====================================================================
def rate_of_rise_ft_hr(series):
    """
    series: list of (epoch_seconds, stage_ft), chronological.
    Returns the least-squares slope over the most recent RATE_WINDOW_MIN,
    expressed in ft/hr (+ = rising).
    """
    if len(series) < 2:
        return 0.0
    t_end = series[-1][0]
    window = [(t, s) for (t, s) in series if t >= t_end - RATE_WINDOW_MIN * 60]
    if len(window) < 2:
        window = series[-2:]
    n = len(window)
    mt = sum(t for t, _ in window) / n
    ms = sum(s for _, s in window) / n
    num = sum((t - mt) * (s - ms) for t, s in window)
    den = sum((t - mt) ** 2 for t, _ in window)
    if den == 0:
        return 0.0
    return (num / den) * 3600.0  # per-sec -> per-hr


def time_to_threshold_hr(stage_ft, rate_ft_hr, level):
    """Hours until `level`'s threshold at the current rise rate (None if N/A)."""
    if rate_ft_hr <= 0 or level not in THRESH:
        return None
    remaining = THRESH[level] - stage_ft
    if remaining <= 0:
        return 0.0
    return remaining / rate_ft_hr


# =====================================================================
# 4. TR-55 DYNAMIC CN  +  GRAPHICAL PEAK DISCHARGE
# =====================================================================
def dynamic_cn(soil_moisture_pct):
    """
    Shift the AMC-II curve number toward dry (AMC-I) or wet (AMC-III)
    based on soil saturation %, via the standard AMC conversions.
    """
    cn2 = CN_NORMAL
    cn1 = cn2 / (2.281 - 0.01281 * cn2)
    cn3 = cn2 / (0.427 + 0.00573 * cn2)
    if soil_moisture_pct is None:
        return cn2
    p = max(0.0, min(100.0, soil_moisture_pct)) / 100.0
    if p <= 0.5:                       # dry half: CN_I -> CN_II
        return cn1 + (cn2 - cn1) * (p / 0.5)
    return cn2 + (cn3 - cn2) * ((p - 0.5) / 0.5)   # wet half: CN_II -> CN_III


def runoff_depth_in(precip_in, cn):
    """SCS runoff depth Q (in) from storm precip and curve number."""
    if cn <= 0:
        return 0.0
    S = 1000.0 / cn - 10.0
    Ia = 0.2 * S
    if precip_in <= Ia:
        return 0.0
    return (precip_in - Ia) ** 2 / (precip_in - Ia + S)


def _unit_peak_q(tc_hr, ia_over_p):
    """TR-55 Type II unit peak discharge q_u (csm/in) by log-quadratic fit."""
    iap = max(0.10, min(0.50, ia_over_p))
    keys = sorted(_TYPE_II)
    lo = max(k for k in keys if k <= iap)
    hi = min(k for k in keys if k >= iap)
    if lo == hi:
        c0, c1, c2 = _TYPE_II[lo]
    else:
        f = (iap - lo) / (hi - lo)
        a, b = _TYPE_II[lo], _TYPE_II[hi]
        c0, c1, c2 = (a[i] + f * (b[i] - a[i]) for i in range(3))
    lt = math.log10(max(0.1, tc_hr))
    return 10.0 ** (c0 + c1 * lt + c2 * lt * lt)


def tr55_peak_discharge_cfs(precip_in, soil_moisture_pct=None):
    """
    TR-55 graphical peak discharge:  q_p = q_u * A * Q * F_p.
    Returns (q_p_cfs, runoff_in, cn_used).
    """
    cn = dynamic_cn(soil_moisture_pct)
    Q = runoff_depth_in(precip_in, cn)
    if Q <= 0:
        return 0.0, 0.0, cn
    S = 1000.0 / cn - 10.0
    Ia = 0.2 * S
    iap = Ia / precip_in if precip_in > 0 else 0.5
    q_u = _unit_peak_q(TIME_OF_CONC_HR, iap)
    q_p = q_u * DRAINAGE_AREA_SQMI * Q * POND_FACTOR
    return q_p, Q, cn


# =====================================================================
# 5. LOGISTIC EARLY-WARNING PROBABILITY
# =====================================================================
def early_warning_probability(stage_ft, rate_ft_hr, soil_pct, storm_rain_in):
    """Weighted logistic blend of the leading indicators -> [0,1]."""
    stage_norm = stage_ft / THRESH["EMERGENCY"]
    rate_norm = (rate_ft_hr / RATE_REF_FT_HR) if rate_ft_hr > 0 else 0.0
    soil_norm = (soil_pct or 0.0) / 100.0
    rain_norm = (storm_rain_in or 0.0) / RAIN_REF_IN
    z = (LOGIT["bias"]
         + LOGIT["stage"] * stage_norm
         + LOGIT["rate"]  * rate_norm
         + LOGIT["soil"]  * soil_norm
         + LOGIT["rain"]  * rain_norm)
    return 1.0 / (1.0 + math.exp(-z))


# =====================================================================
# ORCHESTRATOR
# =====================================================================
@dataclass
class FloodAssessment:
    stage_ft: float
    level: str
    discharge_cfs: float
    rate_ft_hr: float
    time_to_next_hr: float = None
    next_level: str = None
    ew_probability: float = 0.0
    tr55_peak_cfs: float = None
    runoff_in: float = None
    cn_used: float = None


def assess(stage_series, prev_level="NORMAL",
           soil_moisture_pct=None, storm_rain_in=None):
    """
    Main entry point. `stage_series` = list of (epoch_seconds, stage_ft).
    The Cloud Function builds this from the latest Firestore stage docs;
    the self-test builds it synthetically.
    """
    if not stage_series:
        raise ValueError("stage_series is empty")
    stage = stage_series[-1][1]
    rate = rate_of_rise_ft_hr(stage_series)
    level = classify_stage(stage, prev_level)
    discharge = mannings_discharge_cfs(stage)

    cur = LEVELS.index(level)
    next_level = LEVELS[cur + 1] if cur < len(LEVELS) - 1 else None
    ttn = time_to_threshold_hr(stage, rate, next_level) if next_level else None

    p = early_warning_probability(stage, rate, soil_moisture_pct, storm_rain_in)

    a = FloodAssessment(stage_ft=round(stage, 2), level=level,
                        discharge_cfs=round(discharge, 1), rate_ft_hr=round(rate, 3),
                        time_to_next_hr=(round(ttn, 2) if ttn is not None else None),
                        next_level=next_level, ew_probability=round(p, 3))
    if storm_rain_in is not None:
        qp, Q, cn = tr55_peak_discharge_cfs(storm_rain_in, soil_moisture_pct)
        a.tr55_peak_cfs, a.runoff_in, a.cn_used = round(qp, 1), round(Q, 3), round(cn, 1)
    return a


# =====================================================================
# SELF-TEST  —  synthetic hydrograph (no live sensors needed)
# =====================================================================
def _synthetic_hydrograph(dt_min=5):
    """
    Build a baseflow -> rising-limb -> peak -> recession stage series,
    sampled every dt_min minutes. Returns list of (epoch_s, stage_ft).
    """
    pts, t = [], 0
    def add(stage, minutes):
        nonlocal t
        steps = minutes // dt_min
        for _ in range(steps):
            pts.append((t * 60, round(stage, 3)))
            t += dt_min
    # 1 h baseflow, then a ~3 h rise to 10.6 ft, then recession
    add(BASEFLOW_FT, 60)
    base, peak, rise_min = BASEFLOW_FT, 10.6, 180
    for k in range(rise_min // dt_min):
        frac = (k + 1) / (rise_min // dt_min)
        stage = base + (peak - base) * (frac ** 1.6)   # accelerating limb
        pts.append((t * 60, round(stage, 3)))
        t += dt_min
    for k in range(120 // dt_min):                     # 2 h recession
        frac = (k + 1) / (120 // dt_min)
        pts.append((t * 60, round(peak - (peak - 6.0) * frac, 3)))
        t += dt_min
    return pts


def _run_self_test():
    series = _synthetic_hydrograph()
    print("=" * 70)
    print("SYNTHETIC HYDROGRAPH SELF-TEST  (no live sensor data)")
    print(f"  channel {CHANNEL_WIDTH_FT:.0f} ft | slope {BED_SLOPE} | "
          f"n_nom {MANNINGS_N_NOM} | HDc {HDC} | area {DRAINAGE_AREA_SQMI} mi^2")
    print("=" * 70)
    header = f"{'t(min)':>6} {'stage':>6} {'rate/hr':>8} {'Q cfs':>9} {'level':>10} {'P(warn)':>8} {'->next':>10}"
    print(header)
    print("-" * len(header))
    prev = "NORMAL"
    last_printed_level = None
    for i in range(2, len(series)):
        window = series[: i + 1]
        a = assess(window, prev_level=prev,
                   soil_moisture_pct=82.0, storm_rain_in=2.4)
        prev = a.level
        t_min = series[i][0] // 60
        # print every 15 min, plus every state change
        change = a.level != last_printed_level
        if t_min % 15 == 0 or change:
            ttn = f"{a.time_to_next_hr}h" if a.time_to_next_hr is not None else "--"
            nxt = a.next_level or "--"
            flag = "  <== STATE CHANGE" if change else ""
            print(f"{t_min:>6} {a.stage_ft:>6} {a.rate_ft_hr:>8} {a.discharge_cfs:>9} "
                  f"{a.level:>10} {a.ew_probability:>8} {nxt:>5}/{ttn:<4}{flag}")
            last_printed_level = a.level
    # final TR-55 readout
    fa = assess(series, soil_moisture_pct=82.0, storm_rain_in=2.4)
    print("-" * len(header))
    print(f"TR-55 (P=2.4 in, soil=82%): CN_used={fa.cn_used}  "
          f"runoff={fa.runoff_in} in  peak={fa.tr55_peak_cfs} cfs")
    print(f"Manning's discharge at thresholds:  "
          f"7ft={mannings_discharge_cfs(7):.0f}  "
          f"9ft={mannings_discharge_cfs(9):.0f}  "
          f"11ft={mannings_discharge_cfs(11):.0f} cfs")


if __name__ == "__main__":
    _run_self_test()
