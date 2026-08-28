"""Per-pixel kinematics from the displacement cube: velocity, acceleration, noise."""
from __future__ import annotations

import numpy as np

from .stack import DisplacementStack


def pixel_kinematics(stack: DisplacementStack, min_valid_fraction: float = 0.6):
    """Fit d(t) = a + v*t + 0.5*acc*t^2 per pixel (t in years, d in mm).

    Returns dict of (H, W) arrays:
      velocity     mm/yr   (linear term at the last epoch: v + acc*t_end)
      velocity_lin mm/yr   (mean linear rate over the window)
      accel        mm/yr^2
      rmse         mm      residual noise
      valid        bool    enough observations to trust the fit
    """
    T, H, W = stack.shape
    t = stack.t_years()
    d = stack.disp.reshape(T, -1)

    valid_n = np.isfinite(d).sum(axis=0)
    valid = valid_n >= max(4, int(min_valid_fraction * T))

    # Quadratic design matrix
    A = np.column_stack([np.ones_like(t), t, 0.5 * t**2])
    coef = np.full((3, d.shape[1]), np.nan)
    resid = np.full(d.shape[1], np.nan)

    complete = np.isfinite(d).all(axis=0)
    if complete.any():
        sol, res, *_ = np.linalg.lstsq(A, d[:, complete], rcond=None)
        coef[:, complete] = sol
        pred = A @ sol
        resid[complete] = np.sqrt(np.nanmean((d[:, complete] - pred) ** 2, axis=0))
    for k in np.where(valid & ~complete)[0]:
        g = np.isfinite(d[:, k])
        sol = np.linalg.lstsq(A[g], d[g, k], rcond=None)[0]
        coef[:, k] = sol
        resid[k] = np.sqrt(np.mean((d[g, k] - A[g] @ sol) ** 2))

    v_lin, acc = coef[1], coef[2]
    v_now = v_lin + acc * t[-1]

    # Standard errors from the fit covariance: Cov = rmse^2 * (A^T A)^-1.
    # v_now = g . coef with g = [0, 1, t_end]  =>  se = rmse * sqrt(g' C g)
    C = np.linalg.inv(A.T @ A)
    g = np.array([0.0, 1.0, t[-1]])
    se_v_factor = float(np.sqrt(g @ C @ g))
    se_a_factor = float(np.sqrt(C[2, 2]))
    noise = np.maximum(resid, 1.5)          # floor: never trust sub-1.5 mm rmse
    se_velocity = noise * se_v_factor
    se_accel = noise * se_a_factor

    shape = (H, W)
    return {
        "velocity": v_now.reshape(shape),
        "velocity_lin": v_lin.reshape(shape),
        "accel": acc.reshape(shape),
        "rmse": resid.reshape(shape),
        "se_velocity": se_velocity.reshape(shape),
        "se_accel": se_accel.reshape(shape),
        "valid": valid.reshape(shape),
    }


def recent_velocity(stack: DisplacementStack, window_epochs: int) -> np.ndarray:
    """Linear LOS velocity (mm/yr) over the last `window_epochs` epochs only."""
    T = stack.shape[0]
    w = min(window_epochs, T)
    t = stack.t_years()[-w:]
    d = stack.disp[-w:].reshape(w, -1)
    t = t - t.mean()
    dm = d - np.nanmean(d, axis=0)
    denom = np.sum(t**2)
    v = np.nansum(t[:, None] * dm, axis=0) / denom
    return v.reshape(stack.shape[1:])
