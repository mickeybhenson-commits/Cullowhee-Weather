"""
orographic.py  —  orographic lift potential (pre-rain leading indicator)
=======================================================================
Quantifies how hard moist air is being forced UP a windward slope, from a
ridge node's wind + BME280 reading plus a fixed per-site terrain descriptor.
This rises BEFORE precipitation does, so it warns the windward sub-basins
(AAHP, Double Springs) ahead of clock 1 (rainfall).

Physics:  w = (V · upslope) * S   (vertical velocity)
          OLP_raw = w * q          (upslope moisture flux ~ rain-making rate)
          q from temp/RH/pressure; S and upslope azimuth fixed from the DEM.

Pure stdlib; run directly for the synthetic self-test.
=======================================================================
"""

import math

# Per windward site terrain descriptor — [SET from the DEM]
#   upslope_azimuth_deg : compass bearing pointing UP the slope (uphill)
#   slope_gradient      : rise/run (tan of slope angle), dimensionless
TERRAIN = {
    "aahp":           {"upslope_azimuth_deg": 315.0, "slope_gradient": 0.25},   # [SET]
    "double_springs": {"upslope_azimuth_deg": 300.0, "slope_gradient": 0.18},   # [SET]
}

OLP_REF = 0.035   # raw OLP (m/s·kg/kg) mapped to index ~1.0 [calibrate vs events]


def sat_vapor_pressure_hpa(temp_c):
    return 6.112 * math.exp(17.62 * temp_c / (243.12 + temp_c))


def specific_humidity_gkg(temp_f, rh_pct, pressure_inhg):
    if temp_f is None or rh_pct is None or pressure_inhg is None:
        return None
    temp_c = (temp_f - 32.0) * 5.0 / 9.0
    p_hpa = pressure_inhg * 33.8639
    e = (rh_pct / 100.0) * sat_vapor_pressure_hpa(temp_c)
    q = 0.622 * e / (p_hpa - 0.378 * e)      # kg/kg
    return q * 1000.0                         # g/kg


def _bearing_diff(a, b):
    d = abs((a - b) % 360.0)
    return d if d <= 180.0 else 360.0 - d


def upslope_wind_mph(wind_speed_mph, wind_from_deg, upslope_azimuth_deg):
    """Component of the wind blowing UP the slope (0 if down/cross-slope)."""
    if wind_speed_mph is None or wind_from_deg is None:
        return 0.0
    wind_toward = (wind_from_deg + 180.0) % 360.0     # met 'from' -> 'toward'
    phi = _bearing_diff(wind_toward, upslope_azimuth_deg)
    return max(0.0, wind_speed_mph * math.cos(math.radians(phi)))


def vertical_velocity_ms(upslope_mph, slope_gradient):
    return (upslope_mph * 0.44704) * slope_gradient   # mph->m/s, times gradient


def lift_potential(site_id, temp_f, rh_pct, pressure_inhg,
                   wind_speed_mph, wind_from_deg):
    terr = TERRAIN.get(site_id)
    if terr is None:
        return None
    q = specific_humidity_gkg(temp_f, rh_pct, pressure_inhg)
    if q is None:
        return None
    up = upslope_wind_mph(wind_speed_mph, wind_from_deg, terr["upslope_azimuth_deg"])
    w = vertical_velocity_ms(up, terr["slope_gradient"])
    raw = w * (q / 1000.0)
    idx = max(0.0, min(1.0, raw / OLP_REF))
    cat = ("NEGLIGIBLE" if idx < 0.25 else "MODERATE" if idx < 0.5
           else "STRONG" if idx < 0.8 else "EXTREME")
    return {"site": site_id, "q_gkg": round(q, 2), "upslope_mph": round(up, 1),
            "w_ms": round(w, 3), "olp_raw": round(raw, 4),
            "olp_index": round(idx, 3), "category": cat}


def _run_self_test():
    print("=" * 70)
    print("OROGRAPHIC LIFT POTENTIAL — synthetic ridge readings")
    print("=" * 70)
    cases = [
        ("Helene-like: warm, near-saturated, 30 mph SE into NW slope",
         "aahp", 68, 98, 26.5, 30, 135),
        ("Moderate moist upslope, 12 mph SE",
         "aahp", 60, 80, 26.6, 12, 135),
        ("Dry westerly (lee side, no lift)",
         "aahp", 55, 35, 26.7, 20, 270),
        ("Double Springs, moist 18 mph from SE",
         "double_springs", 64, 90, 27.0, 18, 130),
    ]
    for label, sid, t, rh, p, ws, wd in cases:
        r = lift_potential(sid, t, rh, p, ws, wd)
        print(f"\n{label}")
        print(f"  q={r['q_gkg']} g/kg  upslope={r['upslope_mph']} mph  "
              f"w={r['w_ms']} m/s  ->  OLP index {r['olp_index']}  [{r['category']}]")


if __name__ == "__main__":
    _run_self_test()
