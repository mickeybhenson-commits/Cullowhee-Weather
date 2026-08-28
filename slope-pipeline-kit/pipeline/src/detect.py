"""Anomaly detection: find spatially coherent clusters of accelerating slope pixels.

Philosophy (after Tordesillas et al., Univ. of Melbourne): the pre-failure signal
is a *small, contiguous* patch whose motion departs from the background — often
<1% of the monitored area — so detection must combine per-pixel kinematics with
spatial clustering and temporal persistence to beat the class imbalance.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from .stack import DisplacementStack
from .timeseries import pixel_kinematics, recent_velocity


@dataclass
class Cluster:
    cluster_id: int
    n_pixels: int
    centroid_rowcol: tuple[float, float]
    centroid_lonlat: tuple[float, float]
    mean_velocity: float          # mm/yr (LOS, negative = away from satellite)
    peak_velocity: float
    mean_accel: float             # mm/yr^2
    mean_slope_deg: float
    mask: np.ndarray = field(repr=False)   # (H, W) bool
    level: str = "ADVISORY"
    forecast: dict | None = None


def detect(stack: DisplacementStack, cfg) -> tuple[dict, list[Cluster]]:
    a = cfg.analysis
    kin = pixel_kinematics(stack, a["min_valid_fraction"])
    v_recent = recent_velocity(stack, cfg.forecast["window_epochs"])

    usable = (
        kin["valid"]
        & (stack.coherence >= a["coherence_threshold"])
        & (stack.slope >= a["slope_min_deg"])
    )

    speed = np.abs(kin["velocity"])
    # statistical significance: velocity must clear its own standard error
    significant = speed > 3.0 * kin["se_velocity"]

    hot = usable & significant & (speed >= a["velocity_alert_mm_yr"])

    # --- spatial clustering: 8-connected components, drop specks -------------
    labels, n = ndimage.label(hot, structure=np.ones((3, 3)))
    clusters: list[Cluster] = []
    for cid in range(1, n + 1):
        mask = labels == cid
        if mask.sum() < a["min_cluster_pixels"]:
            continue
        rows, cols = np.where(mask)
        rc = (float(rows.mean()), float(cols.mean()))
        clusters.append(
            Cluster(
                cluster_id=len(clusters) + 1,
                n_pixels=int(mask.sum()),
                centroid_rowcol=rc,
                centroid_lonlat=stack.pixel_lonlat(int(rc[0]), int(rc[1])),
                mean_velocity=float(np.nanmean(kin["velocity"][mask])),
                peak_velocity=float(
                    kin["velocity"][mask][np.nanargmax(np.abs(kin["velocity"][mask]))]
                ),
                mean_accel=float(np.nanmean(kin["accel"][mask])),
                mean_slope_deg=float(np.nanmean(stack.slope[mask])),
                mask=mask,
            )
        )

    # --- temporal persistence + escalation -----------------------------------
    for c in clusters:
        sig_accel = abs(c.mean_accel) >= 2.0 * float(np.nanmean(kin["se_accel"][c.mask]))
        accelerating = sig_accel and abs(c.mean_accel) >= a["accel_alert_mm_yr2"]
        recent_fast = np.nanmean(np.abs(v_recent[c.mask])) >= a["velocity_alert_mm_yr"]
        persistent = _persistent(stack, c.mask, a)
        if accelerating and recent_fast and persistent:
            c.level = "WATCH"
        elif not persistent:
            c.level = "ADVISORY"

    fields = {**kin, "v_recent": v_recent, "usable": usable, "hot": hot}
    return fields, clusters


def _persistent(stack: DisplacementStack, mask: np.ndarray, a) -> bool:
    """Motion must persist statistically, not by a fragile all-steps-monotone test.

    Lesson from the Aug 2026 run: cluster 71 moved near-monotonically for a
    year but one dipping epoch broke the strict 3-step monotonic gate, leaving
    it ADVISORY while noise jumps made WATCH. Now: over the last 6+ epochs,
    >=75% of the steps must share the direction of the net motion.
    """
    n = max(a["persistence_epochs"], 6)
    if stack.shape[0] < n + 1:
        return False
    series = np.nanmean(stack.disp[:, mask], axis=1)
    steps = np.diff(series[-(n + 1):])
    if not np.isfinite(steps).any():
        return False
    sign = np.sign(np.nansum(steps))
    if sign == 0:
        return False
    agree = float(np.mean(np.sign(steps) == sign))
    return agree >= 0.75
