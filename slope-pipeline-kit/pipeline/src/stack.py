"""Build a displacement time-series cube from interferogram pairs (SBAS-lite).

Input : per-pair LOS displacement rasters (HyP3 *_los_disp.tif) + coherence maps
Output: DisplacementStack — dates[t], LOS displacement cube disp[t, y, x] in mm,
        mean coherence, and a validity mask.

The pairwise-to-epoch inversion is a standard small-baseline (SBAS) least
squares: each interferogram observes the displacement difference between its
two epochs; we solve for per-epoch displacement relative to the first date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np


@dataclass
class DisplacementStack:
    dates: list[date]              # epoch dates, ascending
    disp: np.ndarray               # (T, H, W) LOS displacement, mm, rel. to epoch 0
    coherence: np.ndarray          # (H, W) mean coherence 0..1
    dem: np.ndarray                # (H, W) elevation, m
    slope: np.ndarray              # (H, W) slope, degrees
    transform: tuple               # (min_lon, min_lat, max_lon, max_lat)

    @property
    def shape(self):
        return self.disp.shape

    def t_years(self) -> np.ndarray:
        d0 = self.dates[0]
        return np.array([(d - d0).days / 365.25 for d in self.dates])

    def pixel_lonlat(self, row: int, col: int) -> tuple[float, float]:
        min_lon, min_lat, max_lon, max_lat = self.transform
        H, W = self.disp.shape[1:]
        lon = min_lon + (col + 0.5) / W * (max_lon - min_lon)
        lat = max_lat - (row + 0.5) / H * (max_lat - min_lat)
        return lon, lat


def slope_from_dem(dem: np.ndarray, pixel_m: float) -> np.ndarray:
    gy, gx = np.gradient(dem, pixel_m)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def sbas_invert(pair_disp: np.ndarray, pair_idx: list[tuple[int, int]], n_epochs: int) -> np.ndarray:
    """Least-squares inversion of pairwise displacements to per-epoch series.

    pair_disp: (P, H, W) displacement of each pair (secondary - reference), mm
    pair_idx : list of (i_ref, i_sec) epoch indices per pair
    Returns  : (T, H, W) epoch displacement relative to epoch 0.
    """
    P, H, W = pair_disp.shape
    # Design matrix over unknowns d_1..d_{T-1} (d_0 = 0 reference)
    A = np.zeros((P, n_epochs - 1))
    for p, (i, j) in enumerate(pair_idx):
        if j > 0:
            A[p, j - 1] = 1.0
        if i > 0:
            A[p, i - 1] = -1.0
    obs = pair_disp.reshape(P, -1)
    good = np.isfinite(obs)
    sol = np.full((n_epochs - 1, obs.shape[1]), np.nan)
    # Solve all pixels with complete observations in one shot; fall back per-pixel otherwise
    complete = good.all(axis=0)
    if complete.any():
        sol[:, complete] = np.linalg.lstsq(A, obs[:, complete], rcond=None)[0]
    for k in np.where(~complete & (good.sum(axis=0) >= n_epochs - 1))[0]:
        g = good[:, k]
        sol[:, k] = np.linalg.lstsq(A[g], obs[g, k], rcond=None)[0]
    out = np.vstack([np.zeros((1, obs.shape[1])), sol])
    return out.reshape(n_epochs, H, W)


def reference_correct(disp: np.ndarray, coherence: np.ndarray, slope: np.ndarray) -> np.ndarray:
    """Tie each epoch to a stable reference: median over high-coherence flat ground.

    Removes orbital/atmospheric bulk offsets that would otherwise look like
    watershed-wide motion.
    """
    ref_mask = (coherence > 0.6) & (slope < 5.0)
    if ref_mask.sum() < 10:
        ref_mask = coherence > np.nanpercentile(coherence, 90)
    offsets = np.nanmedian(disp[:, ref_mask], axis=1)
    return disp - offsets[:, None, None]


def atmospheric_filter(disp: np.ndarray, t_years: np.ndarray, sigma_px: float = 12.0) -> np.ndarray:
    """Remove atmospheric phase screens: temporal high-pass, spatial low-pass.

    Deformation is steady/accelerating in time and localized in space;
    atmosphere is random epoch-to-epoch and smooth over kilometres. So: fit a
    linear trend per pixel, spatially smooth each epoch's *residual*, and
    subtract that smooth screen (the classic MintPy/SBAS mitigation).
    """
    from scipy.ndimage import gaussian_filter

    T = disp.shape[0]
    A = np.column_stack([np.ones_like(t_years), t_years])
    flat = disp.reshape(T, -1)
    fill = np.where(np.isfinite(flat), flat, 0.0)
    coefs = np.linalg.lstsq(A, fill, rcond=None)[0]
    resid = (flat - A @ coefs).reshape(disp.shape)

    out = disp.copy()
    for k in range(T):
        r = np.where(np.isfinite(resid[k]), resid[k], 0.0)
        w = np.isfinite(resid[k]).astype(float)
        screen = gaussian_filter(r, sigma_px) / np.maximum(gaussian_filter(w, sigma_px), 1e-6)
        out[k] = disp[k] - screen
    return out


def load_hyp3_stack(data_dir: Path, cfg) -> DisplacementStack:
    """Load real HyP3 INSAR_GAMMA products into a DisplacementStack.

    Unzips product archives if needed, reprojects every *_los_disp.tif and
    *_corr.tif onto a common ~80 m lat/lon grid over the AOI, then runs the
    same SBAS inversion / referencing / atmospheric filtering as the demo.
    LOS displacement is converted m -> mm (HyP3: positive = toward satellite).
    """
    import re
    import zipfile
    from datetime import datetime

    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.warp import reproject

    # 1. unzip anything not yet unzipped
    for z in sorted(Path(data_dir).glob("*.zip")):
        if not (data_dir / z.stem).exists():
            print("unzipping", z.name)
            zipfile.ZipFile(z).extractall(data_dir)

    disp_files = sorted(Path(data_dir).glob("**/*_los_disp.tif"))
    if not disp_files:
        raise FileNotFoundError(
            f"No *_los_disp.tif found under {data_dir}. Run "
            "'python -m src.process_insar download' first."
        )

    min_lon, min_lat, max_lon, max_lat = cfg.bbox
    # grid at ~80 m, sized from the AOI (so widening the bbox widens coverage)
    import math
    mid_lat = math.radians((min_lat + max_lat) / 2)
    W = max(60, int(round((max_lon - min_lon) * 111320 * math.cos(mid_lat) / 80)))
    H = max(60, int(round((max_lat - min_lat) * 110574 / 80)))
    print(f"analysis grid: {H} x {W} @ ~80 m")
    dst_transform = from_bounds(min_lon, min_lat, max_lon, max_lat, W, H)

    def warp(path, resampling):
        with rasterio.open(path) as src:
            dst = np.full((H, W), np.nan, np.float32)
            reproject(
                rasterio.band(src, 1), dst,
                dst_transform=dst_transform, dst_crs="EPSG:4326",
                src_nodata=src.nodata, dst_nodata=np.nan,
                resampling=resampling,
            )
        # HyP3 uses 0 as nodata in some layers; treat exact zeros at the edge as missing
        return dst

    date_re = re.compile(r"(\d{8}T\d{6})_(\d{8}T\d{6})")
    pair_map = {}
    dem = None
    for f in disp_files:
        m = date_re.search(f.name)
        if not m:
            continue
        d0 = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").date()
        d1 = datetime.strptime(m.group(2), "%Y%m%dT%H%M%S").date()
        if d1 <= d0:
            d0, d1 = d1, d0
        disp_mm = warp(f, Resampling.bilinear) * 1000.0
        corr_files = list(f.parent.glob("*_corr.tif"))
        corr = warp(corr_files[0], Resampling.average) if corr_files else np.full((H, W), np.nan, np.float32)
        pair_map[(d0, d1)] = (disp_mm, corr)
        if dem is None:
            dem_files = list(f.parent.glob("*_dem.tif"))
            if dem_files:
                dem = warp(dem_files[0], Resampling.bilinear)
        print(f"loaded pair {d0} -> {d1}  ({f.parent.name[:40]})")

    if len(pair_map) < 3:
        raise RuntimeError(f"Only {len(pair_map)} usable pairs — not enough for a time series.")

    epochs = sorted({d for k in pair_map for d in k})
    idx = {d: i for i, d in enumerate(epochs)}
    keys = list(pair_map.keys())
    pair_disp = np.stack([pair_map[k][0] for k in keys])
    pair_idx = [(idx[a], idx[b]) for a, b in keys]
    coh = np.nanmean(np.stack([pair_map[k][1] for k in keys]), axis=0)
    coh = np.nan_to_num(coh, nan=0.0)

    if dem is None:
        dem = np.zeros((H, W), np.float32)
        print("WARNING: no *_dem.tif found — slope filtering disabled (slope=45 everywhere)")
        slope = np.full((H, W), 45.0, np.float32)
    else:
        dem = np.nan_to_num(dem, nan=float(np.nanmedian(dem)))
        slope = slope_from_dem(dem, 80.0)

    print(f"SBAS inversion: {len(keys)} pairs -> {len(epochs)} epochs "
          f"({epochs[0]} .. {epochs[-1]})")
    disp = sbas_invert(pair_disp, pair_idx, len(epochs))
    disp = reference_correct(disp, coh, slope)
    t_yr = np.array([(d - epochs[0]).days / 365.25 for d in epochs])
    disp = atmospheric_filter(disp, t_yr, sigma_px=12.0)

    return DisplacementStack(
        dates=epochs, disp=disp, coherence=coh, dem=dem, slope=slope,
        transform=cfg.bbox,
    )
