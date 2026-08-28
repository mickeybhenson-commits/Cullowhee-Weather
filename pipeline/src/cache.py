"""The incremental pair cache: pipeline/state/pair_cache.npz.

Why this file exists
--------------------
A HyP3 INSAR_GAMMA product is ~100 MB of rasters per pair, and by the end of a
second winter this stack will hold ~200 pairs. That archive can never live in
the repo. But every run needs *all* the pairs, because the SBAS inversion is
over the whole network, not just the new edge.

So the run keeps the only part it will ever need again: each pair's LOS
displacement and coherence, already warped onto the ~80 m analysis grid that
`stack.analysis_grid` derives from the config bbox. That is ~24 k pixels per
layer instead of tens of millions. The raw downloads are deleted at the end of
the step that warps them.

The cache is keyed by (reference scene, secondary scene) so a resumed or
re-run bootstrap never re-downloads a pair it already holds, and it stores the
bbox and grid shape it was built on: widen the AOI in config.yaml and the
cache is correctly rejected as stale rather than silently mixing two grids.

Storage: float32, np.savez_compressed. If the file ever exceeds 90 MB (GitHub
warns at 50 MB and hard-rejects at 100 MB) it is rewritten as float16, which
halves it at a resolution of ~0.06 mm at 100 mm — far below the ~4 mm leaf-off
noise floor, so nothing measurable is lost.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np

from .config import ROOT
from .ledger import STATE_DIR, pair_key
from .stack import (
    DisplacementStack,
    analysis_grid,
    atmospheric_filter,
    make_warper,
    reference_correct,
    sbas_invert,
    slope_from_dem,
)

CACHE_PATH = STATE_DIR / "pair_cache.npz"
SIZE_LIMIT_MB = 90.0

# HyP3 product directories carry both acquisition timestamps in their name.
DATE_RE = re.compile(r"(\d{8}T\d{6})_(\d{8}T\d{6})")


@dataclass
class PairCache:
    """Warped pairs, ready for SBAS. All arrays are on the analysis grid."""

    bbox: tuple[float, float, float, float]
    shape: tuple[int, int]                      # (H, W)
    keys: list[str] = field(default_factory=list)      # ledger pair keys
    d0: list[str] = field(default_factory=list)        # ISO reference dates
    d1: list[str] = field(default_factory=list)        # ISO secondary dates
    disp: list[np.ndarray] = field(default_factory=list)   # mm, (H, W) float32
    corr: list[np.ndarray] = field(default_factory=list)   # 0..1, (H, W) float32
    dem: np.ndarray | None = None                          # m, (H, W) float32
    dtype: str = "float32"

    def __len__(self) -> int:
        return len(self.keys)

    @property
    def epochs(self) -> list[str]:
        return sorted({d for pair in zip(self.d0, self.d1) for d in pair})

    def has(self, key: str) -> bool:
        return key in set(self.keys)

    def add(self, key: str, d0: str, d1: str, disp: np.ndarray,
            corr: np.ndarray, dem: np.ndarray | None = None) -> None:
        if self.has(key):
            return
        if disp.shape != self.shape:
            raise ValueError(f"pair {key}: grid {disp.shape} != cache grid {self.shape}")
        self.keys.append(key)
        self.d0.append(d0)
        self.d1.append(d1)
        self.disp.append(np.asarray(disp, np.float32))
        self.corr.append(np.asarray(corr, np.float32))
        if self.dem is None and dem is not None:
            self.dem = np.asarray(dem, np.float32)


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def empty_cache(cfg) -> PairCache:
    return PairCache(bbox=tuple(cfg.bbox), shape=analysis_grid(cfg))


def load(cfg, path: Path = CACHE_PATH) -> tuple[PairCache, str]:
    """Load the cache. Returns (cache, note); a stale cache comes back empty."""
    shape = analysis_grid(cfg)
    if not Path(path).exists():
        return empty_cache(cfg), "no cache on disk — bootstrap required"

    z = np.load(path, allow_pickle=False)
    bbox = tuple(float(v) for v in z["bbox"])
    cached_shape = tuple(int(v) for v in z["shape"])
    if cached_shape != shape or not np.allclose(bbox, cfg.bbox, atol=1e-9):
        return empty_cache(cfg), (
            f"cache is stale: built for bbox {bbox} grid {cached_shape}, "
            f"config now wants {tuple(cfg.bbox)} grid {shape} — rebuild required"
        )

    disp = np.asarray(z["disp"], np.float32)
    corr = np.asarray(z["corr"], np.float32)
    cache = PairCache(
        bbox=bbox,
        shape=shape,
        keys=[str(k) for k in z["keys"]],
        d0=[str(d) for d in z["d0"]],
        d1=[str(d) for d in z["d1"]],
        disp=[disp[i] for i in range(disp.shape[0])],
        corr=[corr[i] for i in range(corr.shape[0])],
        dem=np.asarray(z["dem"], np.float32) if "dem" in z.files else None,
        dtype=str(z["dtype"]) if "dtype" in z.files else "float32",
    )
    return cache, f"{len(cache)} pairs, {len(cache.epochs)} epochs"


def save(cache: PairCache, path: Path = CACHE_PATH) -> Path:
    """Write the cache, dropping to float16 only if float32 would be too big."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    for dtype in ("float32", "float16"):
        _write(cache, path, dtype)
        size_mb = path.stat().st_size / 1e6
        cache.dtype = dtype
        if size_mb <= SIZE_LIMIT_MB or dtype == "float16":
            print(f"pair cache: {len(cache)} pairs, {size_mb:.1f} MB ({dtype})")
            if size_mb > SIZE_LIMIT_MB:
                print(f"::warning::pair_cache.npz is {size_mb:.1f} MB even as float16 — "
                      "time to move it out of the repo (LFS or a release asset)")
            return path
        print(f"pair cache float32 would be {size_mb:.1f} MB (> {SIZE_LIMIT_MB:.0f} MB) "
              "— rewriting as float16")
    return path


def _write(cache: PairCache, path: Path, dtype: str) -> None:
    n = len(cache)
    H, W = cache.shape
    disp = (np.stack(cache.disp) if n else np.zeros((0, H, W), np.float32)).astype(dtype)
    corr = (np.stack(cache.corr) if n else np.zeros((0, H, W), np.float32)).astype(dtype)
    payload = dict(
        bbox=np.asarray(cache.bbox, np.float64),
        shape=np.asarray(cache.shape, np.int32),
        keys=np.asarray(cache.keys, dtype=object).astype("U") if n else np.zeros(0, "U1"),
        d0=np.asarray(cache.d0, dtype=object).astype("U") if n else np.zeros(0, "U1"),
        d1=np.asarray(cache.d1, dtype=object).astype("U") if n else np.zeros(0, "U1"),
        disp=disp,
        corr=corr,
        dtype=np.asarray(dtype),
    )
    if cache.dem is not None:
        payload["dem"] = cache.dem.astype(np.float32)
    np.savez_compressed(path, **payload)


# --------------------------------------------------------------------------
# ingest: HyP3 product directory -> cache entries
# --------------------------------------------------------------------------

def product_dates(name: str) -> tuple[str, str] | None:
    m = DATE_RE.search(name)
    if not m:
        return None
    a = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").date()
    b = datetime.strptime(m.group(2), "%Y%m%dT%H%M%S").date()
    if b < a:
        a, b = b, a
    return a.isoformat(), b.isoformat()


def ingest_products(cache: PairCache, cfg, data_dir: Path,
                    key_for: dict[str, str] | None = None) -> list[str]:
    """Warp every un-cached HyP3 product under data_dir into the cache.

    key_for maps a product directory name to its ledger pair key; products with
    no mapping fall back to a key built from the two acquisition dates, which is
    what a manual drop-in of products gets.
    Returns the list of pair keys added.
    """
    from rasterio.enums import Resampling

    warp = make_warper(cfg)
    added: list[str] = []

    for disp_tif in sorted(Path(data_dir).glob("**/*_los_disp.tif")):
        prod = disp_tif.parent.name
        dates = product_dates(disp_tif.name) or product_dates(prod)
        if not dates:
            print(f"  skipping {prod}: no acquisition dates in the name")
            continue
        d0, d1 = dates
        key = (key_for or {}).get(prod) or pair_key(f"date:{d0}", f"date:{d1}")
        if cache.has(key):
            continue

        disp_mm = warp(disp_tif, Resampling.bilinear) * 1000.0
        corr_files = list(disp_tif.parent.glob("*_corr.tif"))
        if corr_files:
            corr = warp(corr_files[0], Resampling.average)
        else:
            corr = np.full(cache.shape, np.nan, np.float32)

        dem = None
        if cache.dem is None:
            dem_files = list(disp_tif.parent.glob("*_dem.tif"))
            if dem_files:
                dem = warp(dem_files[0], Resampling.bilinear)

        cache.add(key, d0, d1, disp_mm, corr, dem)
        added.append(key)
        print(f"  cached pair {d0} -> {d1}  ({prod[:44]})")

    return added


# --------------------------------------------------------------------------
# cache -> DisplacementStack (identical maths to stack.load_hyp3_stack)
# --------------------------------------------------------------------------

def stack_from_cache(cache: PairCache, cfg) -> DisplacementStack:
    """SBAS-invert the full cached pair set into an epoch displacement cube.

    Same inversion, referencing and atmospheric filtering as
    stack.load_hyp3_stack — only the source of the warped pairs differs.
    """
    if len(cache) < 3:
        raise RuntimeError(
            f"Only {len(cache)} cached pairs — not enough for a time series."
        )

    H, W = cache.shape
    epoch_strs = cache.epochs
    epochs = [date.fromisoformat(d) for d in epoch_strs]
    idx = {d: i for i, d in enumerate(epoch_strs)}

    pair_disp = np.stack(cache.disp)
    pair_idx = [(idx[a], idx[b]) for a, b in zip(cache.d0, cache.d1)]
    coh = np.nanmean(np.stack(cache.corr), axis=0)
    coh = np.nan_to_num(coh, nan=0.0)

    if cache.dem is None:
        dem = np.zeros((H, W), np.float32)
        print("WARNING: no DEM in the cache — slope filtering disabled (slope=45 everywhere)")
        slope = np.full((H, W), 45.0, np.float32)
    else:
        dem = np.nan_to_num(cache.dem, nan=float(np.nanmedian(cache.dem)))
        slope = slope_from_dem(dem, 80.0)

    print(f"SBAS inversion: {len(cache)} pairs -> {len(epochs)} epochs "
          f"({epochs[0]} .. {epochs[-1]})")
    disp = sbas_invert(pair_disp, pair_idx, len(epochs))
    disp = reference_correct(disp, coh, slope)
    t_yr = np.array([(d - epochs[0]).days / 365.25 for d in epochs])
    disp = atmospheric_filter(disp, t_yr, sigma_px=12.0)

    return DisplacementStack(
        dates=epochs, disp=disp, coherence=coh, dem=dem, slope=slope,
        transform=cfg.bbox,
    )


def epoch_noise_mm(stack: DisplacementStack, usable: np.ndarray) -> list[float]:
    """Robust spatial scatter of each epoch, in mm — the honest noise floor.

    Median absolute deviation scaled to a Gaussian sigma, over the pixels the
    detector is willing to use. This is the series behind the noise-by-epoch
    chart, which stays on the page because it is the context for every call:
    leaf-on summer runs about twice as noisy as leaf-off winter.
    """
    out = []
    for k in range(stack.shape[0]):
        v = stack.disp[k][usable]
        v = v[np.isfinite(v)]
        if v.size < 20:
            out.append(float("nan"))
            continue
        out.append(round(float(1.4826 * np.median(np.abs(v - np.median(v)))), 2))
    return out
