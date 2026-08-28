"""Inverse-velocity failure forecasting (Fukuzono 1985; Carlà et al. 2017).

For slopes in tertiary (accelerating) creep, 1/velocity trends linearly toward
zero; the x-intercept of that line is the forecast failure time. This is the
workhorse of operational slope monitoring (open-pit mining radar), applied here
to satellite time series.

Honest caveat baked into the gates: with a 6–12 day Sentinel-1 revisit this
gives you a horizon of weeks, not hours — suitable for prioritizing ground
instrumentation and inspection, not for last-minute evacuation.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np

from .detect import Cluster
from .stack import DisplacementStack


def inverse_velocity_forecast(stack: DisplacementStack, cluster: Cluster, cfg) -> dict | None:
    f = cfg.forecast
    w = min(f["window_epochs"], stack.shape[0] - 1)
    series = np.nanmean(stack.disp[:, cluster.mask], axis=1)  # mm
    t = stack.t_years() * 365.25                              # days

    # short-term smoothing before differencing (Carlà et al. 2017): raw
    # epoch-to-epoch velocities are too jittery for a stable 1/v regression
    k = np.array([0.25, 0.5, 0.25])
    padded = np.concatenate([[series[0]], series, [series[-1]]])  # edge-replicate
    sm = np.convolve(padded, k, mode="valid")

    # incremental velocities (mm/day) at epoch midpoints, last w intervals
    dt = np.diff(t)[-w:]
    dv = np.diff(sm)[-w:] / dt
    tm = (t[:-1] + np.diff(t) / 2)[-w:]

    sign = np.sign(np.nanmean(dv))
    v = dv * sign                       # rectify so motion is positive
    ok = v > 0.05                       # need real motion (>0.05 mm/day)
    if ok.sum() < 4:
        return None

    # the window must actually be accelerating, not just fast
    half = len(v) // 2
    v_early, v_late = np.nanmean(v[:half]), np.nanmean(v[half:])
    if v_early <= 0 or v_late / max(v_early, 1e-6) < f["min_accel_ratio"]:
        return None

    inv_v = 1.0 / v[ok]
    x = tm[ok]
    # linear fit 1/v = a + b*t ; failure when 1/v -> 0  =>  t_f = -a/b.
    b, a = np.polyfit(x, inv_v, 1)
    pred = a + b * x
    ss_res = np.sum((inv_v - pred) ** 2)
    ss_tot = np.sum((inv_v - inv_v.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    if b >= 0 or r2 < f["min_r2"]:
        return None                     # not converging toward failure / too noisy

    t_fail_days = -a / b
    days_out = t_fail_days - t[-1]
    if days_out <= 0:
        days_out = 0.0
    forecast_date = stack.dates[-1] + timedelta(days=float(days_out))

    return {
        "method": "inverse_velocity",
        "confidence": "high" if r2 >= 0.7 else "moderate",
        "r2": round(float(r2), 3),
        "days_to_failure": round(float(days_out), 1),
        "forecast_date": forecast_date.isoformat(),
        "within_warning_horizon": bool(days_out <= f["warning_horizon_days"]),
        "window_epochs": int(w),
    }
